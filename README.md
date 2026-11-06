# MilanNews → AC Milan UZ

MilanNews.it rasmiy RSS tasmasidagi barcha mavzularni har **30 daqiqada** tekshiradigan Python bot. To‘liq maqoladan manbaga tayangan o‘zbekcha lotin posti tayyorlaydi, maqolaning asl fotosuratini oladi va `@acmilannews_uzb` kanaliga yuboradi. Sun’iy rasm yaratilmaydi.

## Ishlash tartibi

1. RSS havolalari SQLite navbatiga saqlanadi. MilanNews raqamli maqola IDsi bir marta ishlanadi; slug o‘zgarishi takroriy post yaratmaydi.
2. Hozirgi MilanNews shablonidagi `#tcc-read [role="main"] .text` to‘liq o‘qiladi, reklama/menyu/tavsiya bloklari chiqariladi. RSS qisqacha matni to‘liq maqola o‘rniga ishlatilmaydi. Olish muvaffaqiyatsiz bo‘lsa `ARTICLE_TEXT_UNAVAILABLE` logi chiqadi va maqola navbatda qoladi.
3. OpenAI uch bosqichda ishlaydi: asl matndan aniq dalillarni ajratish, post yozish, to‘liq manbaga qarshi alohida tahririy tekshiruv. Muhim ism, sabab, misol, raqam va fikr egasi saqlanishi tekshiriladi. Sifat/til/uzunlik tekshiruvidan o‘tmasa qayta yoziladi yoki keyingi urinishga qoladi.
4. Ko‘rinadigan **butun post** sarlavha, emoji, manba va imzo bilan 1000 UTF-16 birlikdan oshmaydi (Telegram chegarasi 1024). HTML teglar va havolaning yashirin URLi Telegram hisobiga kirmaydi. Matn gap o‘rtasidan kesilmaydi.
5. Haqiqiy rasm yuklanmasa matnli xabar yuboriladi. Tarjima muvaffaqiyatsiz bo‘lsa italyancha xabar yuborilmaydi.

RSS tekshiruvi va navbatni qayta ishlash mustaqil ishlaydi: uzoq tarjima yangi 30 daqiqalik tekshiruvni to‘xtatmaydi. Bitta muvaffaqiyatli maqola odatda 3 ta OpenAI so‘rovini talab qiladi; cheklangan qayta yozish va API qayta urinishlari bilan eng ko‘pi 14 ta so‘rov bo‘lishi mumkin. Model JSON javob rejimini qo‘llashi kerak. `temperature` yuborilmaydi.

## Render sozlamalari — faqat egasi tasdiqlaganidan keyin

Ushbu o‘zgarishlar Render’ga joylanmagan. `render.yaml` ko‘rib chiqish uchun namuna; uni qo‘llash ham deploy hisoblanadi. Mavjud xizmatni yangi xizmat bilan almashtirmang.

| Sozlama | Qiymat |
|---|---|
| Service type | Background Worker, bitta worker |
| Python | 3.12.x |
| Build command | `pip install -r requirements.txt` |
| Start command | `python main.py` |
| Persistent Disk | Mavjud disk, `/var/data` mount |
| `DB_PATH` | **Mavjud qiymatni saqlang**, odatda `/var/data/milan_seen.sqlite3` |
| `BOT_TOKEN` | Mavjud maxfiy Telegram tokeni |
| `CHAT_ID` | `@acmilannews_uzb` |
| `OPENAI_API_KEY` | Mavjud maxfiy OpenAI kaliti |
| `OPENAI_MODEL` | `gpt-4.1-mini` yoki JSON rejimli mos model |
| `DRY_RUN` | Dastlab `true`; egasi postlarni ko‘rib chiqqach `false` |
| `TEST_POST` | `false` |
| Auto deploy | **Off**, qo‘lda tasdiqlangan deploy |

Bot kanal administratori bo‘lishi va xabar joylash huquqiga ega bo‘lishi kerak. Environment qiymatlarini kodga yoki GitHub’ga yozmang. `.env.example` faqat namuna; dastur `.env` faylini avtomatik o‘qimaydi.

Deploy oldidan worker’ni to‘xtatib mavjud SQLite faylini zaxiralang. Mavjud jadval qo‘shimcha ustunlar bilan avtomatik yangilanadi, `sent`, `seed` va `seeded_v1` tarixi saqlanadi. Eski nusxaga qaytishda worker’ni to‘xtatib, zarur bo‘lsa zaxiradan tiklang. Persistent Disk bo‘lmasa restartda tarix yo‘qolib takroriy postlar chiqishi mumkin. Bitta disk va `flock` bitta jarayon ishlashini ta’minlaydi; alohida diskli bir nechta bot nusxasini ishga tushirmang.

Yangi, bo‘sh bazada birinchi RSS tasmasi mavjud xulqni saqlab `seed` qilinadi: tarixiy postlar birdan kanalga yuborilmaydi, keyingi tekshiruvda topilgan yangi maqolalar yuboriladi. `TEST_POST=true` birinchi tasmaning bitta xabarini joylaydi, shuning uchun ishlab turgan xizmatda `false` qolsin. Mavjud bazadagi eski `sent` holati avvalgi post sifatsiz bo‘lsa ham avtomatik o‘zgartirilmaydi.

## Xatolar va yetkazilishi noaniq xabarlar

Tarjima, maqolani olish yoki aniq Telegram rad javobi (`429`, ruxsat yoki format xatosi) keyingi siklda qayta ishlanadi. Tayyor post SQLite’da saqlanadi; qayta yuborishda tarjima takrorlanishi shart emas. RSS ishlamasa ham bazadagi navbat qayta ishlanadi.

Telegram xabarni qabul qilib, HTTP javobi yo‘qolishi mumkin. Telegram Bot API idempotency kaliti bermaydi, shuning uchun SQLite bilan birgalikda mutlaq “exactly once” kafolati yo‘q. Timeout, yuborish paytidagi uzilish yoki restart holatida yozuv `delivery_unknown` bo‘ladi va **avtomatik qayta yuborilmaydi**. Bu xabarni kanal tarixidan tekshirish kerak. Tekshirmasdan qayta navbatga qo‘ymang.

```bash
python scripts/queue_admin.py list
# Kanalda xabar borligini qo‘lda tekshirgandan keyin:
python scripts/queue_admin.py resolve --article-id 635347 --resolution sent --message-id 123 --confirm-reviewed
# Kanalda xabar YO‘QLIGINI qo‘lda tekshirgandan keyin:
python scripts/queue_admin.py resolve --article-id 635347 --resolution retry --confirm-reviewed
```

Yozuv holatini o‘zgartirishdan avval worker’ni to‘xtating; vosita bazaning jarayon qulfini oladi. Tasdiqlangan xabar qayta yuborilmaydi. Noaniq yuborishdan keyingi tekshiruvsiz yo‘qotishsiz va takrorsiz avtomatik yetkazishni bir vaqtda kafolatlab bo‘lmaydi.

## Vaqt

`Europe/Rome` → `Asia/Tashkent` uchun `zoneinfo` ishlatiladi. Qishda +4, yozda +3 soat, sananing keyingi kunga o‘tishi hisobga olinadi. Soat **Italiya vaqti** deb aniq ko‘rsatilishi va sana ma’lum bo‘lishi kerak. Hududi, sanasi yoki DST o‘tishidagi soati noaniq bo‘lsa vaqt taxmin qilinmaydi; postdan chiqariladi.

## Sinov va kanalga yubormaydigan preview

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/check_sources.py --output /tmp/milannews-sources.json
python scripts/preview_articles.py --output /tmp/acmilan-preview.json
# OPENAI_API_KEY xavfsiz Environment orqali berilgan bo‘lsa:
python scripts/preview_articles.py --refresh-source --live-openai --output /tmp/acmilan-live-preview.json
```

`preview_articles.py` va `check_sources.py` Telegram’ga hech narsa yubormaydi. `--live-openai` bo‘lmasa haqiqiy tarjima yaratilmaydi. Jonli OpenAI preview pulli API so‘rovlaridan foydalanadi; yozilgan JSONdagi postlarni mazmun, tabiiy o‘zbekcha til va fikr egasi bo‘yicha ko‘rib chiqing. Kalit yo‘q bo‘lsa kod 2 bilan aniq to‘xtaydi.

Testlar uchta haqiqiy sahifadan olingan kichraytirilgan HTML va to‘liq matn fixture’laridan foydalanadi. `tests/fixtures/manifest.json` URL, olish vaqti, HTTP status, asl javob SHA-256 va matn uzunligini saqlaydi. Model kontraktlari oldindan tayyorlangan javoblar bilan tekshiriladi; bu jonli model o‘zbekchasining inson bahosini almashtirmaydi. Batafsil dalillar: [audit hisobot](docs/AUDIT.md).

## RSS qamrovi chegarasi

2026-11-06 tekshiruvda rasmiy RSS faqat oxirgi 20 maqolani qaytardi. Bot topilgan barcha mavzularni saqlaydi, filtr qo‘ymaydi, 30 daqiqalik interval o‘zgarmaydi. Ammo uzoq uzilish yoki 30 daqiqada 20 tadan ko‘p yangi maqola chiqsa, tasmadan tushgan havolalarni RSSning o‘zi qaytara olmaydi. `RSS_WINDOW` logi qamrovni, oldingi tasma bilan umumiy ID qolmasa `RSS_COVERAGE_GAP` ehtimoliy bo‘shliqni aniq ko‘rsatadi. Bunday holatda MilanNews rasmiy arxividan tekshirilgan backfill kerak; boshqa saytdan maqola qo‘shilmaydi. Bu versiya cheksiz tarixiy qamrovni da’vo qilmaydi.
