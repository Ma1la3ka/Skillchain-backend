-- Run once in TiDB before deploying. Safe to re-run.
CREATE TABLE IF NOT EXISTS wallet_ledger (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id    INT NOT NULL,
  direction  VARCHAR(10) NOT NULL,            -- 'credit' | 'debit'
  amount     DECIMAL(14,2) NOT NULL,
  reference  VARCHAR(100) NOT NULL,
  note       VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_ledger_ref (reference, direction),   -- a job can only ever credit once, a payout only debit once
  KEY idx_ledger_user (user_id)
);

CREATE TABLE IF NOT EXISTS payouts (
  id               BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id          INT NOT NULL,
  amount           DECIMAL(14,2) NOT NULL,
  bank_code        VARCHAR(10) NOT NULL,
  bank_name        VARCHAR(100),
  account_no       VARCHAR(20) NOT NULL,
  account_name     VARCHAR(150),
  reference        VARCHAR(100),
  status           VARCHAR(20) NOT NULL DEFAULT 'processing',  -- processing | success | failed | needs_review
  provider_message VARCHAR(255),
  created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_payout_status (status),
  KEY idx_payout_user (user_id)
);

-- Before go-live ONLY if your current balances are test data:
-- UPDATE users SET escrow_balance = 0, total_withdrawn = 0;
