"""Pydantic 请求/响应模型"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ---- 商品 ----
class ProductBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    emoji: str = Field(default="🛍️", max_length=8)   # 与 DB 列 String(8) 一致;前端也会转义
    image_url: str = Field(default="", max_length=255)  # 商品图片 URL(空 = 用 emoji 代替)
    price: int = Field(ge=0)
    original_price: int = Field(default=0, ge=0)
    stock: int = Field(default=0, ge=0)
    tag: str = ""
    is_active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    emoji: Optional[str] = Field(default=None, max_length=8)
    image_url: Optional[str] = Field(default=None, max_length=255)
    price: Optional[int] = Field(default=None, ge=0)
    original_price: Optional[int] = Field(default=None, ge=0)
    stock: Optional[int] = Field(default=None, ge=0)
    tag: Optional[str] = None
    is_active: Optional[bool] = None


class ProductOut(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---- 购物车 ----
class CartAdd(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=99)


class CartItemOut(BaseModel):
    id: int
    product: ProductOut
    quantity: int
    model_config = ConfigDict(from_attributes=True)


# ---- 订单(模拟支付) ----
class CheckoutIn(BaseModel):
    payment_method: str = "credit_card"   # credit_card / convenience_store / paypay


class OrderItemOut(BaseModel):
    product_name: str
    unit_price: int
    quantity: int
    model_config = ConfigDict(from_attributes=True)


class OrderOut(BaseModel):
    id: int
    nickname: str
    total: int
    payment_method: str
    status: str
    created_at: datetime
    items: list[OrderItemOut]
    model_config = ConfigDict(from_attributes=True)


# ---- 统计(管理后台) ----
class StatsOut(BaseModel):
    total_orders: int
    total_revenue: int
    total_comments: int
    total_users: int
    active_products: int
    current_viewers: int
    peak_viewers: int
    bot_enabled: bool
    top_products: list[dict]
