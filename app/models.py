"""ORM のモデル(テーブル定義)"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, CheckConstraint, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    """画面に渡す時刻の形(ISO 8601、UTC、秒まで)。SQLite から読んだ時刻はタイムゾーンを持たないので UTC として扱う"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))   # 形式: pbkdf2_sha256$回数$ソルト$ハッシュ(旧形式 ソルト$ハッシュ も検証できる)
    # ゲスト(登録せずに参加した人)。パスワードはなく(password_hash は検証できない値)、名前は「ゲスト1234」の形
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuthSession(Base):
    """ログインのセッション:1人が複数持てる(Web ブラウザと iOS アプリなど、複数の端末で同時にログイン)

    DB にはトークンの SHA-256 ハッシュだけを保存する:DB が漏れても、ハッシュからは利用者になりすませない。
    """
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))       # Cookie で認証するときの CSRF 対策(二重送信)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Product(Base):
    __tablename__ = "products"
    # 最後の砦:アプリのコードに不具合があっても、在庫や価格がマイナスになる更新は DB が拒否する
    __table_args__ = (
        CheckConstraint("stock >= 0", name="ck_products_stock_nonneg"),
        CheckConstraint("price >= 0 AND original_price >= 0", name="ck_products_price_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    emoji: Mapped[str] = mapped_column(String(8), default="🛍️")               # 画像がない場合の代替表示
    image_url: Mapped[str] = mapped_column(String(255), default="", server_default="")  # 商品画像(/static/products/xxx.png)
    price: Mapped[int] = mapped_column(Integer)                        # 単価(円)
    original_price: Mapped[int] = mapped_column(Integer, default=0)    # 通常価格(取り消し線で表示する価格)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    tag: Mapped[str] = mapped_column(String(30), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CartItem(Base):
    __tablename__ = "cart_items"
    # 同じユーザーのカートに同じ商品は1行だけ(同時に「カートへ」が押されても重複行を作らせない)
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),
        CheckConstraint("quantity >= 1 AND quantity <= 99", name="ck_cart_quantity_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    product: Mapped[Product] = relationship(lazy="joined")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(40))                  # 注文時点のユーザー名(あとで変わっても注文の記録は変えない)
    total: Mapped[int] = mapped_column(Integer)
    payment_method: Mapped[str] = mapped_column(String(30), default="credit_card")
    status: Mapped[str] = mapped_column(String(20), default="paid")    # 決済は模擬:確定した時点で支払い済み
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", lazy="selectin")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(120))             # 注文時点の商品名(あとで商品名が変わっても注文の記録は変えない)
    unit_price: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)  # BOT・AI ホストのコメントは空
    nickname: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    is_host: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
