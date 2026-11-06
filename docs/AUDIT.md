# Tekshiruv va tuzatish hisoboti

Tekshirilgan boshlang‘ich commit: `4aa9305` (`main`). Mavjud `main.py` va `requirements.txt` o‘zgartirishdan oldin to‘liq o‘qildi. Asosiy bot funksiyalari — MilanNews-only RSS, 30 daqiqa, kanal imzosi, Environment Variables, SQLite, birinchi ishga tushishdagi seed va asl fotosuratdan foydalanish — saqlandi. Render’ga deploy va Telegram kanaliga sinov xabari yuborish bajarilmadi.

## Aniqlangan sabablar

| Muammo | Dalil va oqibat | Tuzatish |
|---|---|---|
| To‘liq maqola olinmaydi | Uchala haqiqiy sahifadagi `.text` konteyneri eski selektorlar orasida yo‘q. JSON-LD’da `articleBody` ham yo‘q; extractor bo‘sh qaytargan. | `#tcc-read [role="main"] .text` to‘liq o‘qiladi, reklama oralig‘idan keyingi paragraflar ham olinadi. |
| RSS kirishi “to‘liq maqola” vazifasini bajargan | `extract_article` olish xatosida RSSga qaytgan, hatto model asl dalillarni ko‘rmagan. | RSS fallback bekor qilindi. Olish xatosi aniq loglanadi, maqola qayta ishlash navbatida qoladi. |
| Oxirgi argumentlar yo‘qolishi mumkin | Eski kod matnni 8000/7500 belgi, vaqt ko‘rsatmasini 6800 belgida kesgan. | To‘liq olingan matn uchala tahririy API bosqichiga yuboriladi. Model kontekstiga sig‘masa API xatosi bo‘lib navbatda qoladi, yashirin kesilmaydi. |
| Sifat uzunlik bilan baholangan | Eski qabul mezoni til markerlari, bo‘limlar soni va minimal belgilar edi; aniq dalillar borligi tekshirilmagan. | Asl iqtibosli fakt ro‘yxati, muhim ism tekshiruvi, yozish va to‘liq manbaga qarshi alohida tahririy tekshiruv. |
| Qisqartirish dalilni buzishi mumkin | `caption_text` eng uzun abzasning oxirini so‘zma-so‘z kesgan. | Butun post 1000 UTF-16 birlikka sig‘masa mazmunni saqlab qayta yoziladi; sig‘maydigan post yuborilmaydi. |
| Vaqt hududi taxmini | Italiya yangilik sayti ekanligi matchning qaysi vaqt hududida berilganini isbotlamaydi; eski kodda lokal vaqtni taxmin qilish mumkin edi. | Aniq Italiya vaqt belgisi va ishonchli sana talab etiladi; DST noaniqligi rad etiladi. |
| Takroriy post | Faqat URL bo‘yicha dedup: slug almashishi yangi xabar hisoblangan. Timeoutdan keyin ham yozuv pending qolgan. | Raqamli maqola IDsi, atomik claim, yuborishdan oldingi SQLite checkpoint, tasdiqlangan message ID; noaniq yetkazish alohida holatda ushlanadi. |
| Uzoq navbat RSS tekshiruvini kechiktirgan | Eski asosiy sikl barcha tarjimalar tugashini kutgan; ular 30 daqiqadan oshsa yangi tekshiruv ham kechikkan. | RSS polling va yetkazish mustaqil asyncio sikllariga ajratildi; bloklangan yetkazish paytida polling davom etishi sinaldi. |
| RSS xatosi backlogni to‘xtatgan | Eski `one_cycle` RSS xatosidan so‘ng darhol qaytgan. | Saqlangan navbat RSSdan mustaqil qayta ishlanadi. |
| Maxfiy ma’lumot logga chiqishi mumkin | `log.exception` ichki HTTP xatolari va tokenli URLlarni chiqarishi mumkin edi. | Xato turi/kodi loglanadi, ildiz log handlerida token redaksiyasi qo‘shildi. |

`temperature` eski joriy versiyada ham yuborilmagan — bu ishlayotgan yechim saqlandi va test bilan mustahkamlandi. JSON rejimiga mos bo‘lmagan model uchun xavfsiz, tushunarli xato qo‘shildi. Transport/429/5xx uchun cheklangan qayta urinish, yaroqsiz JSON uchun bitta tuzatish urinishi bor. Odatda maqolaga 3 API chaqiruvi; eng ko‘p 14 urinish (barcha bosqichlar qayta yozilsa va har bir so‘rov qayta urinilsa).

## Uchta real maqola

2026-11-06 UTC’da asl sahifalar HTTP 200 bilan olindi. Minimized HTML va to‘liq matn `tests/fixtures/` ichida; asl to‘liq HTTP javob SHA-256, olish vaqti va sarlavha `manifest.json`da. Qayta jonli tekshiruv natijasi: [source-check.json](source-check.json).

| Maqola | To‘liq matn | Saqlanishi tekshirilgan mazmun |
|---|---:|---|
| Pasotto, 635356 | 1539 belgi | To‘rtta Yevropa o‘yinida uchinchi yomon chiqish, 2:0, 14–16 ochko, eng yaxshi 24 jamoa, ekspert xulosasi. |
| Letizia, 635347 | 1696 belgi | A/B/C toifalar, Tammy Abraham, 20 millionlik transfer siyosatining foydali xaridlari, Pulisic, Terracciano–Cremonese misoli, tarkib qurilishi haqidagi xulosa. |
| Bucciantini, 635306 | 971 belgi | Bologna misoli, Amorimning Rabiot/Moreirani derbiga asrashi, Modric/Pulisic/Rabiot/Moreira, kerakli ruhiyat butun tarkibga singmaganligi haqidagi fikr. |

Uchala asl fotosurat ham yuklandi va tekshirildi: JPEG, 1200×674. Telegram uchun qayta kodlangan hajmlar 69 726 / 84 910 / 83 516 bayt. Rasm qayta kodlanadi, sun’iy tasvir yaratilmaydi.

## Sinovlar va ularning chegarasi

**73 ta avtomatik test o‘tdi** (Python 3.12.14, 1.469 soniya).

Avtomatik testlar matnni to‘liq ajratish, oxirgi argumentlar, menyu/reklama tozalash, RSS fallbackni bloklash, muhim dalil/ism/fikr egasi, yakuniy caption chegarasi, Italyancha/Kirill yozuvini rad etish, API xatolari, qish/yoz/DST/kun-yil almashishi, ID dedup, SQLite migratsiya/qulf/restart, timeout va qayta urinish, rasm va navbatni qo‘lda tekshirishni qamraydi.

Manbaga qarshi tayyorlangan o‘zbekcha test postlari va model javoblari **test fixture’lari**. Ular API o‘rniga mock qilinadi; jonli modelning tarjima sifati sinaldi degan ma’noni bildirmaydi. Bu muhitda `OPENAI_API_KEY` yo‘q: jonli preview aniq kod 2 bilan to‘xtadi, OpenAI va Telegram so‘rovi yuborilmadi. Jonli model sifatini baholash uchun README’dagi `--live-openai` preview’ni kalit sozlangan muhitda bajarib, JSONdagi postlarni o‘qib chiqish kerak.

Test logi: [test-results.txt](test-results.txt). `compileall`, `pip check` va `git diff --check` ham bajarildi. GitHub Actions bir xil unittest to‘plamini bajaradi va deploy qilmaydi. Sinov muhiti: Python 3.12.14; requests 2.34.2; beautifulsoup4 4.15.0; openai 2.54.0; python-telegram-bot 22.8; Pillow 12.3.0; tzdata 2026.6.

## Ishonchlilik va haqiqiy cheklovlar

- Rasmiy RSS faqat oxirgi **20 ta homepage maqolasi**ni qaytaradi: [rasmiy izoh](https://www.milannews.it/info_rss/). Tekshiruvda 17:30:41–20:30:02 UTC oralig‘i ko‘rindi. Uzoq uzilish va keskin ko‘p nashr holatida tasmadan tushib ketgan yangi maqolalarni RSSning o‘zi qaytara olmaydi. Qamrov bo‘shlig‘i loglanadi. To‘liq tarixiy qamrov uchun MilanNews rasmiy arxivining tekshirilgan backfill integratsiyasi kerak; ushbu o‘zgarish buni amalga oshirilgan deb da’vo qilmaydi.
- Telegram’da idempotency kaliti yo‘q. Javobi yo‘qolgan yuborishni bazadan bilib bo‘lmaydi. `delivery_unknown` avtomatik qayta yuborishni bloklaydi; administrator kanalni tekshiradi va `scripts/queue_admin.py` bilan hal qiladi. Tekshiruvsiz mutlaq takrorsiz va yo‘qotishsiz yetkazish kafolati berilmaydi.
- SQLite bazasi va Render Persistent Disk yo‘li saqlanishi, bitta Linux worker ishlashi zarur. Yangi bazadagi birinchi feed tarixiy postlarni ommaviy yubormaslik uchun eski seed xulqini saqlaydi.
- OpenAI sifati ehtimollik xususiyatiga ega. Mustaqil so‘rovdagi tahririy tekshiruv qat’iy himoya beradi, lekin inson muharririning ma’noni baholashini mutlaq almashtirmaydi.

## GitHub holati

Alohida branch: `fix/full-article-editorial-pipeline`. Loyiha egasi tekshirilgan `main.py` va `requirements.txt` fayllarini qo‘lda yukladi va [Draft PR #1](https://github.com/ahadxonmurodillayev-commits/acmilan-bot/pull/1) ni yaratdi. Ushbu ikki fayl tayyorlangan nusxalar bilan solishtirildi. GitHub integratsiyasining kod yozish va PR yaratish urinishlari HTTP 403 `Resource not accessible by integration` bilan rad etildi; hisobga kirishning o‘zi integratsiyaga yozish huquqini bermagan. Testlar, namunalar, yordamchi skriptlar va hujjatlar ham shu PR uchun tayyorlangan. PR tavsifi [PULL_REQUEST.md](PULL_REQUEST.md)da. `main`ga merge va Render deploy bajarilmagan.
