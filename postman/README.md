# TasKode Postman API Collection & Environments

This directory contains pre-configured Postman assets for testing, debugging, and automating the **TasKode** API.

---

## Files Included

1. **`TasKode.postman_collection.json`**:
   The full Postman Collection (v2.1.0) containing all API endpoints grouped into organized folders:
   - **Authentication**: Direct JWT login (`/api/token/`), Token Refresh (`/api/token/refresh/`), User Registration (`/api/register/`), and 2FA Two-Step Login (`/api/login/` + `/api/2fa/verify/`).
   - **Projects**: Project CRUD, GitHub AST synchronization, and live Celery/ChromaDB indexing status polling.
   - **Tasks**: Task CRUD and Gemini AI Natural Language task generation (`/api/tasks/gen/`).
   - **Calendar Sync**: iCalendar/Webcal token generation, rotation, and public `.ics` subscription feed.
   - **Users**: User listing and detail retrieval.
   - **API Schema & Documentation**: Raw OpenAPI 3.0 schema and Swagger UI endpoints.

2. **`TasKode.local.postman_environment.json`**:
   Environment file configuring `baseUrl` to `http://127.0.0.1:8000` alongside variable placeholders (`access_token`, `refresh_token`, etc.).

3. **`schema.yml`**:
   The active OpenAPI 3.0 specification exported directly from `drf-spectacular`.

---

## How to Import into Postman

1. Open **Postman**.
2. Click the **Import** button in the top left corner.
3. Select and import both:
   - `TasKode.postman_collection.json`
   - `TasKode.local.postman_environment.json`
4. In the top-right environment selector of Postman, choose **"TasKode (Local Dev)"**.

---

## How It Works (Authentication & Variable Chaining)

### 1. Zero-Friction Bearer Token Auth
The collection defines a top-level Bearer authorization:
```
Type: Bearer Token
Token: {{access_token}}
```
Every endpoint in `Projects`, `Tasks`, `Calendar Sync`, and `Users` inherits this setting automatically.

### 2. Automatic JWT Token Capture
When you call `1. Authentication > Obtain JWT Token Pair (Direct Login)` with your username & password:
- The **Post-response Test Script** automatically extracts `access` and `refresh` from the response.
- It updates the collection/environment variables `{{access_token}}` and `{{refresh_token}}`.
- You do **not** need to manually copy-paste tokens into authorization headers.

### 3. Automatic Resource Chaining
- When you run **Create Project**, the script automatically stores `{{project_id}}`.
- When you run **Create Task**, the script automatically stores `{{task_id}}`.
- When you run **Get Calendar Subscription URLs**, it stores `{{calendar_token}}`.
- Subsequent `GET`, `PATCH`, and `DELETE` requests reference these variables directly.

---

## Running Headless API Tests (Newman CLI)

You can run automated tests from the terminal or in CI/CD pipelines using [Newman](https://www.npmjs.com/package/newman):

```bash
# Run against local server
npx newman run postman/TasKode.postman_collection.json -e postman/TasKode.local.postman_environment.json
```
