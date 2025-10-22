from dotenv import load_dotenv

from .database.connection import get_database

load_dotenv()

if __name__ == "__main__":
    db = get_database()
    print(db.list_collection_names())