import os, traceback
from dotenv import load_dotenv

# give load_dotenv an explicit path so it never needs to guess the caller frame
load_dotenv(dotenv_path=".env")

print("KEY prefix:", (os.getenv("LLM_API_KEY") or "")[:12])
print("BASE_URL  :", os.getenv("LLM_BASE_URL"))
print("MODEL     :", os.getenv("LLM_MODEL"))
print("-" * 40)

from openai import OpenAI
client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL") or None,
)
try:
    r = client.chat.completions.create(
        model=os.getenv("LLM_MODEL") or "gpt-4o-mini",
        messages=[{"role": "user", "content": "Reply with one word: ok"}],
        max_tokens=10,
    )
    print("SUCCESS:", r.choices[0].message.content)
except Exception:
    print("FAILED:")
    traceback.print_exc()
