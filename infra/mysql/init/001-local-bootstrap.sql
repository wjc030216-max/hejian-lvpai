CREATE DATABASE IF NOT EXISTS aitravel_dev
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'aitravel_dev'@'%' IDENTIFIED BY 'aitravel_dev_password';
GRANT ALL PRIVILEGES ON aitravel_dev.* TO 'aitravel_dev'@'%';
FLUSH PRIVILEGES;
