# Code Msingi
How to replace everyone:
## Learn the fundamentals that make you hireable, then use AI to multiply what you can do.
Master the fundamentals. Let AI make you 100×. That's what makes you hireable.

Buy our E-Books and master the fundamentals.

## API scaffold

This repository includes a small FastAPI API scaffold with SQLite, SQLAlchemy ORM, an application factory, and modular routers.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for the interactive API. The `ebooks.db` SQLite database is created automatically at startup.

Included endpoints: `GET /health`, `GET /api/v1/ebooks/`, `POST /api/v1/ebooks/`, and `GET /api/v1/ebooks/{ebook_id}`.

The template landing page is available at `/`. Add browser assets in `app/static/css`, `app/static/js`, and `app/static/images`; add server-rendered pages in `app/templates` and register their routes in `app/web.py`.

## Book uploads

Visit `/admin/books/new` to upload a PDF and enter its title, short description, extended description, and price. Each PDF is stored in `app/media/ebooks`; its first page is rendered to a JPEG in `app/media/thumbnails` and displayed in the storefront. The admin route is intentionally unauthenticated in this starter scaffold—protect it with authentication and authorization before deploying.

The listing's Download link opens `/books/{book_id}/checkout` for paid books, which presents full book details and payment-method choices. Set a book's price to `0` in the uploader to mark it **Free**: its Download link serves the PDF immediately. The payment button is intentionally a UI placeholder until a payment provider is selected and integrated.

Every book also has a shareable public page at `/books/{book_id}`. It presents a direct download for free books and the checkout path for paid books.

Paid checkout supports the asynchronous M-Pesa flow at `/payments/mpesa`. The selected book and checkout reference are stored in the signed session; Safaricom's callback at `/payments/mpesa/callback` records the final payment state before allowing the paid download. Configure the Daraja credentials and a public HTTPS callback URL before sending real payment prompts.

The starter automatically adds newly introduced nullable e-book columns to an existing SQLite database at startup, preserving current records. Use Alembic migrations once the schema becomes production-critical.
