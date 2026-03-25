from app import app
from database import create_database

# Create tables if they don't exist
create_database()

if __name__ == "__main__":
    app.run()