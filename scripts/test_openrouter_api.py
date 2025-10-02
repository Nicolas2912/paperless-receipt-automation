from openai import OpenAI
from dotenv import load_dotenv
import os


dotenv_path=r"C:\Users\Anwender\Desktop\Nicolas\Dokumente\MeineProgramme\paperless-receipt-automation\.env"
dotenv_path_win = r"C:\Users\Anwender\Desktop\Nicolas\Dokumente\MeineProgramme\paperless-receipt-automation\.env"


load_dotenv(dotenv_path_win=r"C:\Users\Anwender\Desktop\Nicolas\Dokumente\MeineProgramme\paperless-receipt-automation\.env")

# Debug output
print(f"File exists: {os.path.exists(dotenv_path)}")  # True/Falses
api_key = os.getenv("OPEN_ROUTER_API_KEY")

client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=api_key,
)

completion = client.chat.completions.create(
  extra_body={},
  model="meta-llama/llama-4-maverick:free",
  messages=[
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "What is in this image?"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"
          }
        }
      ]
    }
  ]
)
print(completion.choices[0].message.content)