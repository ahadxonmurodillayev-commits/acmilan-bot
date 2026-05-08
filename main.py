import requests
from bs4 import BeautifulSoup
from telegram import Bot
import time

TOKEN = "8607058778:AAExM3hiBhVaQiPlQ7RhfVuErYuEpEkaIwY"
CHAT_ID = "@acmilan_news_uz"

bot = Bot(token=TOKEN)

sent_links = set()

def get_news():
    url = "https://m.milannews.it/"
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

        text = f"""🚨🔴⚫ AC Milan yangiliklari

📰 {title}

📌 Batafsil:
{link}

#ACMilan

🔴⚫ AC Milan yangiliklari
"""

        bot.send_message(
            chat_id=CHAT_ID,
            text=text
        )

while True:
    send_news()
    time.sleep(10800)
