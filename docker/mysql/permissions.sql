-- Runtime query account. Rotate this placeholder password before deployment.
CREATE USER IF NOT EXISTS 'shopkeeper_ro'@'%' IDENTIFIED BY 'change-me-initial-password';
GRANT SELECT, SHOW VIEW ON dw.* TO 'shopkeeper_ro'@'%';
FLUSH PRIVILEGES;
