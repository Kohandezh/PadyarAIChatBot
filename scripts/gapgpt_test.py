import asyncio
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv

load_dotenv()  # reads the project .env from the current working directory
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

async def test_gapgpt():
    client = AsyncOpenAI(api_key=OPENAI_API_KEY, base_url="https://api.gapgpt.app/v1")
    try:
        r = await client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role":"user","content":"سلام"}]
        )
        print(r.choices[0].message.content)
    except Exception as e:
        print("ERROR:", e)

asyncio.run(test_gapgpt())
