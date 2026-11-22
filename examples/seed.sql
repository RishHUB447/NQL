-- Small demo shop. Portable across PostgreSQL, MySQL/MariaDB and SQLite.

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL,
    country VARCHAR(50) NOT NULL,
    signup_date DATE NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50) NOT NULL,
    price DECIMAL(10, 2) NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers (id)
);

CREATE TABLE order_items (
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    PRIMARY KEY (order_id, product_id),
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

INSERT INTO customers (id, name, email, country, signup_date) VALUES
(1, 'Asha Rao', 'asha@example.com', 'India', '2024-01-15'),
(2, 'Ben Carter', 'ben@example.com', 'USA', '2024-02-03'),
(3, 'Chloe Martin', 'chloe@example.com', 'France', '2024-03-22'),
(4, 'Diego Lopez', 'diego@example.com', 'Spain', '2024-05-10'),
(5, 'Emma Schmidt', 'emma@example.com', 'Germany', '2024-06-18'),
(6, 'Farhan Ali', 'farhan@example.com', 'India', '2024-08-01'),
(7, 'Grace Kim', 'grace@example.com', 'USA', '2025-01-09'),
(8, 'Hiro Tanaka', 'hiro@example.com', 'Japan', '2025-03-14');

INSERT INTO products (id, name, category, price) VALUES
(1, 'Mechanical Keyboard', 'Electronics', 89.00),
(2, 'Wireless Mouse', 'Electronics', 25.50),
(3, '27in Monitor', 'Electronics', 229.99),
(4, 'Desk Lamp', 'Home', 34.00),
(5, 'Standing Desk', 'Furniture', 399.00),
(6, 'Office Chair', 'Furniture', 249.00),
(7, 'Notebook Pack', 'Stationery', 12.00),
(8, 'Gel Pens', 'Stationery', 6.50);

INSERT INTO orders (id, customer_id, order_date, status) VALUES
(1, 1, '2024-02-01', 'delivered'),
(2, 2, '2024-02-20', 'delivered'),
(3, 1, '2024-04-11', 'delivered'),
(4, 3, '2024-05-02', 'refunded'),
(5, 4, '2024-07-19', 'delivered'),
(6, 5, '2024-09-05', 'delivered'),
(7, 2, '2024-11-29', 'delivered'),
(8, 6, '2025-01-12', 'shipped'),
(9, 7, '2025-02-14', 'delivered'),
(10, 1, '2025-03-03', 'delivered'),
(11, 8, '2025-04-21', 'pending'),
(12, 5, '2025-05-30', 'cancelled'),
(13, 3, '2025-06-08', 'delivered'),
(14, 6, '2025-07-17', 'shipped');

INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
(1, 1, 1, 89.00), (1, 2, 1, 25.50),
(2, 3, 2, 229.99),
(3, 7, 5, 12.00), (3, 8, 10, 6.50),
(4, 5, 1, 399.00),
(5, 6, 1, 249.00), (5, 4, 2, 34.00),
(6, 1, 1, 89.00), (6, 3, 1, 229.99),
(7, 5, 1, 399.00), (7, 6, 1, 249.00),
(8, 2, 3, 25.50),
(9, 4, 1, 34.00), (9, 7, 2, 12.00),
(10, 3, 1, 219.99),
(11, 1, 2, 89.00),
(12, 6, 1, 249.00),
(13, 8, 4, 6.50), (13, 2, 1, 25.50),
(14, 5, 1, 379.00);
