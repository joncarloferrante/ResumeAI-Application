import getpass
import sqlite3

from auth_utils import hash_password
from database import create_user, get_user_by_email, init_db, update_user_role


def normalize_email(email: str) -> str:
    normalized_email = email.lower().strip()
    if "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
        raise ValueError("Enter a valid email address.")

    return normalized_email


def main() -> int:
    init_db()

    try:
        email = normalize_email(input("Admin email: "))
    except ValueError as exc:
        print(exc)
        return 1

    existing_user = get_user_by_email(email)
    if existing_user:
        if existing_user.get("role") == "admin":
            print(f"Admin user already exists: {email}")
            return 1
        else:
            update_user_role(email, "admin")
            print(f"Existing user promoted to admin: {email}")
            return 0

    password = getpass.getpass("Admin password: ")
    confirm_password = getpass.getpass("Confirm password: ")

    if password != confirm_password:
        print("Passwords do not match.")
        return 1

    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1

    try:
        create_user(email, hash_password(password), role="admin")
    except sqlite3.IntegrityError:
        print(f"User already exists: {email}")
        return 1

    print(f"Admin user created: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
