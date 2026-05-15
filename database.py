import mysql.connector

def init_db():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="Ola01/2007"
        )
        c = conn.cursor()

        c.execute("CREATE DATABASE IF NOT EXISTS skillchain")
        c.execute("USE skillchain")
        print("✨ Database 'skillchain' is ready.")

        queries = [

            # ── 1. USERS ──────────────────────────────────────────────────────
            # Fixes: role uses ENUM (not CHECK — MySQL ignored CHECK before 8.0.16)
            # Added: profile_photo_url, is_active, last_login
            """
            CREATE TABLE IF NOT EXISTS users (
                id                   INT PRIMARY KEY AUTO_INCREMENT,
                name                 VARCHAR(255) NOT NULL,
                email                VARCHAR(255) UNIQUE NOT NULL,
                password_hash        VARCHAR(255) NOT NULL,
                role                 ENUM('worker', 'client') NOT NULL,
                phone                VARCHAR(20),
                trade                VARCHAR(100),
                trust_score          DOUBLE DEFAULT 0.0,
                jobs_completed       INT DEFAULT 0,
                profile_photo_url    VARCHAR(500),
                squad_account_number VARCHAR(50),
                squad_bank_name      VARCHAR(100),
                squad_customer_id    VARCHAR(100),
                is_active            TINYINT(1) DEFAULT 1,
                last_login           TIMESTAMP NULL DEFAULT NULL,
                created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,

            # ── 2. JOBS ───────────────────────────────────────────────────────
            # Fixes: status uses ENUM, verified_at/paid_at explicitly DEFAULT NULL
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id                  INT PRIMARY KEY AUTO_INCREMENT,
                client_id           INT NOT NULL,
                worker_id           INT,
                title               VARCHAR(255) NOT NULL,
                description         TEXT,
                site_address        TEXT NOT NULL,
                site_lat            DOUBLE NOT NULL,
                site_lng            DOUBLE NOT NULL,
                amount              DOUBLE NOT NULL,
                status              ENUM(
                                      'open',
                                      'assigned',
                                      'pending_verification',
                                      'verified',
                                      'paid',
                                      'disputed'
                                    ) DEFAULT 'open',
                worker_lat          DOUBLE,
                worker_lng          DOUBLE,
                distance_meters     DOUBLE,
                video_filename      VARCHAR(255),
                escrow_reference    VARCHAR(255),
                transfer_reference  VARCHAR(255),
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_at         TIMESTAMP NULL DEFAULT NULL,
                paid_at             TIMESTAMP NULL DEFAULT NULL,
                FOREIGN KEY (client_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (worker_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """,

            # ── 3. VERIFICATION LOGS ──────────────────────────────────────────
            # Every GPS check — pass or fail — is logged here permanently
            """
            CREATE TABLE IF NOT EXISTS verification_logs (
                id               INT PRIMARY KEY AUTO_INCREMENT,
                job_id           INT NOT NULL,
                worker_id        INT NOT NULL,
                worker_lat       DOUBLE NOT NULL,
                worker_lng       DOUBLE NOT NULL,
                site_lat         DOUBLE NOT NULL,
                site_lng         DOUBLE NOT NULL,
                distance_meters  DOUBLE NOT NULL,
                passed           TINYINT(1) NOT NULL,
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id)    REFERENCES jobs(id)  ON DELETE CASCADE,
                FOREIGN KEY (worker_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """,

            # ── 4. REVIEWS ────────────────────────────────────────────────────
            # Stores the actual review text + rating after a job is paid.
            # Trust score on users table is the computed average — this is the raw data.
            """
            CREATE TABLE IF NOT EXISTS reviews (
                id           INT PRIMARY KEY AUTO_INCREMENT,
                job_id       INT NOT NULL UNIQUE,
                reviewer_id  INT NOT NULL,
                worker_id    INT NOT NULL,
                rating       TINYINT NOT NULL CHECK(rating BETWEEN 1 AND 5),
                comment      TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id)      REFERENCES jobs(id)  ON DELETE CASCADE,
                FOREIGN KEY (reviewer_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (worker_id)   REFERENCES users(id) ON DELETE CASCADE
            )
            """,

            # ── 5. NOTIFICATIONS ──────────────────────────────────────────────
            # Persists all alerts: job requests, review pings, wallet credits.
            # is_read lets you show the unread dot on the bell icon.
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id          INT PRIMARY KEY AUTO_INCREMENT,
                user_id     INT NOT NULL,
                type        VARCHAR(50) NOT NULL,
                title       VARCHAR(255) NOT NULL,
                message     TEXT NOT NULL,
                is_read     TINYINT(1) DEFAULT 0,
                link        VARCHAR(500),
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """,

            # ── 6. PASSWORD RESET TOKENS ──────────────────────────────────────
            # Needed for "Forgot password" flow. Token is emailed to user.
            # used flag prevents replay attacks.
            """
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id         INT PRIMARY KEY AUTO_INCREMENT,
                user_id    INT NOT NULL,
                token      VARCHAR(255) UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used       TINYINT(1) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """,

        ]

        for query in queries:
            c.execute(query)

        conn.commit()
        print("✅ All tables created successfully.")
        print()
        print("Tables in skillchain database:")
        c.execute("SHOW TABLES")
        for (table,) in c.fetchall():
            print(f"  • {table}")

    except mysql.connector.Error as err:
        print(f"❌ MySQL Error: {err}")

    finally:
        if 'conn' in locals() and conn.is_connected():
            c.close()
            conn.close()
            print("\n🔌 Connection closed.")


if __name__ == "__main__":
    init_db()