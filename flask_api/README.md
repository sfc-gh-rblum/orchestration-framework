# Flask API

A RESTful API built with Flask that provides basic CRUD operations for managing items.

## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python app.py
```

The API will be available at `http://localhost:5000`

## API Endpoints

### Health Check
- GET `/health` - Check if the API is running

### Items
- GET `/api/items` - Get all items
- GET `/api/items/<id>` - Get a specific item
- POST `/api/items` - Create a new item
- PUT `/api/items/<id>` - Update an existing item
- DELETE `/api/items/<id>` - Delete an item

### Request Body Format (POST/PUT)
```json
{
    "name": "Item name",
    "description": "Item description"
}
```

## Environment Variables
Create a `.env` file in the root directory with the following variables:
```
PORT=5000  # Optional, defaults to 5000
```
