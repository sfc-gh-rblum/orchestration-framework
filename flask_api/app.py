from flask import Flask, request
from flask_cors import CORS
from flask_restful import Api, Resource
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)
api = Api(app)

# Sample in-memory data store for prompts
prompts = []


class PromptResource(Resource):
    def get(self, prompt_id=None):
        if prompt_id is None:
            return prompts

        for prompt in prompts:
            if prompt["id"] == prompt_id:
                return prompt
        return {"message": "Prompt not found"}, 404

    def post(self):
        data = request.get_json()
        if not data or "prompt" not in data:
            return {"message": "Invalid data - prompt is required"}, 400

        prompt = data["prompt"]
        try:
            # For now, just echo back a simple response
            response = {
                "output": "You can ask questions about Snowflake's business, product offerings, performance, and S&P500 company metrics. For example:\n1. What are Snowflake's key products?\n2. How has Snowflake's revenue grown?\n3. What is the market cap of tech companies in the S&P500?\n4. Compare Snowflake's performance with other cloud companies.",
                "sources": [],
            }
            new_prompt = {
                "id": len(prompts) + 1,
                "prompt": prompt,
                "response": response,
            }
            prompts.append(new_prompt)
            return new_prompt, 201
        except Exception as e:
            return {"message": f"Error processing prompt: {str(e)}"}, 500


# Register routes
api.add_resource(PromptResource, "/api/prompt", "/api/prompt/<int:prompt_id>")


# Health check endpoint
@app.route("/health")
def health_check():
    return {"status": "healthy"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
