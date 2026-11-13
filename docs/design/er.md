# ER 図

`app/models.py` と `migrations/versions/` の内容を図にしたもの。

```mermaid
erDiagram
  users ||--o{ auth_sessions : "ログイン(複数端末)"
  users ||--o{ cart_items : "カート"
  users ||--o{ orders : "注文"
  users |o--o{ comments : "コメント(BOT/ホストは null)"
  products ||--o{ cart_items : "カートに入る"
  orders ||--|{ order_items : "明細"

  users {
    int id PK
    varchar username UK "2〜20文字"
    varchar password_hash "salt$pbkdf2"
    datetime created_at
  }
  auth_sessions {
    int id PK
    int user_id FK
    varchar token_hash UK "SHA-256(トークン本体は保存しない)"
    varchar csrf_token
    datetime created_at
    datetime expires_at "有効期限"
  }
  products {
    int id PK
    varchar name
    text description
    varchar emoji
    int price "円"
    int original_price "通常価格"
    int stock "在庫"
    varchar tag
    bool is_active "false=販売停止(論理削除)"
    datetime created_at
  }
  cart_items {
    int id PK
    int user_id FK
    int product_id FK
    int quantity "1〜99"
  }
  orders {
    int id PK
    int user_id FK
    varchar nickname "注文時のユーザー名"
    int total
    varchar payment_method "credit_card / convenience_store / paypay"
    varchar status "paid"
    datetime created_at
  }
  order_items {
    int id PK
    int order_id FK
    int product_id "FKなし(スナップショット)"
    varchar product_name "注文時の商品名"
    int unit_price "注文時の価格"
    int quantity
  }
  comments {
    int id PK
    int user_id FK "nullable"
    varchar nickname
    text text
    bool is_host
    bool is_bot
    datetime created_at
  }
```

## 設計のポイント
- **order_items は商品名・単価を「スナップショット」として保存**:注文後に商品の価格や名前が変わっても、過去の注文の金額は変わらない。
- **商品は物理削除せず is_active=false(論理削除)**:過去の注文から参照できなくなるのを防ぐ。
- **auth_sessions を users から分離**:1人が Web と iOS で同時にログインでき、端末ごとにログアウトできる。
