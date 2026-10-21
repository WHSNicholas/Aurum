# Aurum Imports
import aurum.database.database as database

print(database.load_transactions(database.open_database())[0])

