# Khmelnytskyi Outage JSON Parser (GitHub Pages)

Легковаговий скрапер і парсер графіків погодинних відключень електроенергії (ГПВ) з сайту [hoe.com.ua](https://hoe.com.ua/page/pogodinni-vidkljuchennja), який зберігає результати у JSON-файли в папці `docs/` та автоматично публікує їх на **GitHub Pages** за допомогою GitHub Actions.

---

## 📁 Структура проєкту

```text
khm_outage_parser/
├── .github/workflows/
│   └── update_and_deploy.yml   # Автоматичний запуск кожні 30 хв + деплой на GitHub Pages
├── src/
│   ├── scraper.py              # Скрапер hoe.com.ua (з ретраями та news fallback)
│   ├── parser.py               # Парсер розкладу, інтервалів та оперативних змін
│   └── exporter.py             # Збереження результатів у JSON з історією
├── docs/                       # Публічна папка для GitHub Pages
│   ├── .nojekyll
│   ├── index.html              # Прев'ю сторінка з посиланнями на JSON
│   ├── outages.json            # Усі доступні дати (до 14 днів)
│   └── latest.json             # Найсвіжіший графік
├── tests/                      # Юніт-тести
├── run.py                      # Точка входу (CLI)
└── requirements.txt
```

---

## 🚀 Локальний запуск

Можна використати вже існуюче `.venv` або створити нове:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Запустити скрапер і зберегти JSON у docs/
python run.py

# Запустити тести
pytest -v
```

Після запуску в папці `docs/` з'являться/оновляться файли:
- `docs/outages.json` — повний JSON з усіма датами
- `docs/latest.json` — JSON тільки з останньою актуальною датою

---

## 🌐 Публікація на GitHub Pages

1. Створіть новий репозиторій на GitHub та запуште цей проєкт:
   ```powershell
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<username>/<repo-name>.git
   git push -u origin main
   ```
2. У репозиторії відкрийте **Settings → Pages → Build and deployment → Source** та оберіть **GitHub Actions**.
3. Workflow `.github/workflows/update_and_deploy.yml` автоматично запуститься при пуші (або вручну через вкладку **Actions → Run workflow**) і далі оновлюватиме JSON **кожні 30 хвилин**.
4. Ваші JSON-файли будуть доступні за адресами (з підтримкою CORS):
   - `https://<username>.github.io/<repo-name>/latest.json`
   - `https://<username>.github.io/<repo-name>/outages.json`
   - `https://<username>.github.io/<repo-name>/` — веб-перегляд

---

## 📦 Формат JSON (`latest.json`)

```json
{
  "source": "https://hoe.com.ua/page/pogodinni-vidkljuchennja",
  "last_updated": "2026-11-21T15:20:00+02:00",
  "date": "2026-11-21",
  "operational_message": "За розпорядженням НЕК «Укренерго» ...",
  "queues": {
    "1.1": {
      "intervals": [
        { "start": "00:00", "end": "04:00", "type": "base" },
        { "start": "09:00", "end": "13:00", "type": "base" }
      ],
      "total_hours_off": 8.0
    }
  }
}
```
