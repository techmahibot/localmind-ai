# LocalMind AI – Database Documentation

## 1. Overview

LocalMind AI uses a database to store registered user accounts and authentication information.

The project supports two database configurations:

- **SQLite:** Used for local development and offline operation.
- **Neon PostgreSQL:** Used as an optional hosted database for the Vercel deployment.

The application automatically selects the database based on the `DATABASE_URL` environment variable.

## 2. Database Architecture

| Environment | Database | Purpose |
|---|---|---|
| Local development | SQLite | Local account storage |
| Offline operation | SQLite | Works without an internet connection |
| Hosted deployment | Neon PostgreSQL | Persistent hosted account storage |

### Database selection

The database connection is managed in `auth.py`.

- If `DATABASE_URL` is configured, the application connects to PostgreSQL.
- If `DATABASE_URL` is not configured, the application uses SQLite.
- On Vercel, a persistent database configuration is required.

## 3. Database Schema

The application creates `localmind_users` for accounts and `localmind_documents`
for extracted document text. Documents belong to the uploading user and survive
backend restarts and separate hosted function instances. Up to 30 recent
documents are kept per account; older entries are removed when new ones are uploaded.

`localmind_documents` stores `id`, `owner_id`, `filename`, `pages_json`
(extracted page text), and `created_at`. The original binary file is not stored.

| Column | Purpose |
|---|---|
| `id` | Unique user identifier |
| `username` | User's original username |
| `username_key` | Normalized username used to enforce case-insensitive uniqueness |
| `password_hash` | Securely hashed password |

### Constraints

- Each user has a unique ID.
- Usernames are unique, regardless of letter case.
- Passwords are never stored as plain text.
- The `username_key` column has a UNIQUE constraint.

For example, `Shristi`, `shristi`, and `SHRISTI` are treated as the same username.

## 4. Database Configuration

### SQLite – Local Development

SQLite is the default database when `DATABASE_URL` is not configured.

The default database file is:

`backend/localmind.db`

To use a different SQLite database file, configure:

`LOCALMIND_DB_PATH`

Example:

```bash
export LOCALMIND_DB_PATH="./localmind.db"
unset DATABASE_URL
```

SQLite supports local and offline operation without requiring a hosted database.

### Neon PostgreSQL – Hosted Deployment

Neon PostgreSQL provides the hosted database used for the optional Vercel deployment.

Configure the following environment variables in the Vercel project settings:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `LOCALMIND_SESSION_SECRET` | Secret used to sign authentication sessions |

The PostgreSQL connection string should be obtained from the Neon project dashboard.

**Security requirements:**

- Never commit database credentials to GitHub.
- Never expose the session secret in frontend code.
- Keep production secrets in Vercel environment variables.
- Use a strong, randomly generated session secret.

Generate a session secret locally using:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 5. Authentication and Password Security

User authentication is implemented in `auth.py`.

### Password hashing

Passwords are hashed using:

- PBKDF2-HMAC-SHA256
- 600,000 iterations
- A randomly generated salt

Password verification uses a constant-time comparison to reduce timing-based information leakage.

### Session authentication

After successful login, the application creates a signed session token.

The token contains:

- User ID
- Session creation timestamp
- HMAC signature

Sessions expire after the configured `SESSION_SECONDS` duration.

The session signature is generated using `LOCALMIND_SESSION_SECRET`.

## 6. Database Initialization

The `ensure_users_table()` function creates the users table if it does not already exist.

The `database_ready()` function checks whether the database
