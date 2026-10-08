import os
import sys

from sarvamai import SarvamAI

api_key = os.environ.get("SARVAM_API_KEY")
if not api_key:
    sys.exit("SARVAM_API_KEY is not set. Add it to .env and load it into the environment.")

client = SarvamAI(api_subscription_key=api_key)

response = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[{"role": "user", "content": "Say hello in Hindi and English in one short line."}],
)

print(response.choices[0].message.content)
