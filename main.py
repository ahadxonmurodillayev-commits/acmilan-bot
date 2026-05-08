import requests
from bs4 import BeautifulSoup
from telegram import Bot
import time
import os
from openai import OpenAI

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=BOT_TOKEN)

client = OpenAI(api_key=OPENAI_API_KEY)

sent_links = set()


def translate_news(title):
    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Sen AC Milan yangiliklarini chiroyli va qisqa tarzda o‘zbek tilida yozadigan futbol sharhlovchisan."
                },
                {
                    "role": "user",
                    "content": f"Ushbu yangilik sarlavhasini o‘zbekchaga qisqa va tushunarli qilib tarjima qil: {title}"
                }
            ],
            max_tokens=100
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"Tarjima xatosi: {e}"


def get_news():
    url = "https://www.milannews.it/"
    res = requests.get(url)
    soup = BeautifulSoup(res.text, "html.parser")

    news = soup.find_all("h2")

    result = []

    for item in news[:3]:
        a = item.find("a")

        if not a:
            continue

        title = a.text.strip()
        link = a["href"]

        if link not in sent_links:
            sent_links.add(link)
            result.append((title, link))

    return result


def send_news():
    news_list = get_news()

    for title, link in news_list:
        uzbek_text = translate_news(title)

        text = f"""
🔴⚫ AC Milan yangiliklari

📰 {uzbek_text}

🔗 Batafsil:
{link}

#ACMilan
"""

        bot.send_message(
            chat_id=CHAT_ID,
            text=text
        )


while True:
    send_news()
    time.sleep(1800)
