USE skillchain;
CREATE TABLE IF NOT EXISTS device_tokens (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id    INT NOT NULL,
  token      VARCHAR(255) NOT NULL,
  platform   VARCHAR(10) DEFAULT 'android',
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_device_token (token),
  KEY idx_device_user (user_id)
);
