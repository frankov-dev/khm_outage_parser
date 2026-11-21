# Khmelnytskyi Outage JSON Parser

Легковаговий скрапер і парсер графіків погодинних відключень електроенергії (ГПВ) з сайту [hoe.com.ua](https://hoe.com.ua/page/pogodinni-vidkljuchennja), який автоматично зберігає актуальні дані у JSON-файли в папці `docs/` через **GitHub Actions**.

---

## 📁 Структура проєкту

```text
khm_outage_parser/
├── .github/workflows/
│   └── update_and_deploy.yml   # Автоматичний запуск кожні 30 хв + коміт JSON у репо
├── src/
│   ├── scraper.py              # Скрапер hoe.com.ua (з ретраями та news fallback)
│   ├── parser.py               # Парсер розкладу, інтервалів та оперативних змін
│   └── exporter.py             # Збереження результатів у JSON з історією
├── docs/
│   ├── outages.json            # Усі доступні дати (до 14 днів)
│   └── latest.json             # Найсвіжіший графік
├── tests/                      # Юніт-тести
├── run.py                      # Точка входу (CLI)
└── requirements.txt
```

---

## 🚀 Локальний запуск

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Запустити скрапер і зберегти JSON у docs/
python run.py

# Запустити тести
pytest -v
```

---

## 📱 Використання в Android / клієнті (Raw JSON)

Після пушу в публічний репозиторій на GitHub workflow автоматично оновлюватиме файли **кожні 30 хвилин**.
Стягувати JSON напряму можна за URL:

- **Останній графік:**
  ```text
  https://raw.githubusercontent.com/<username>/<repo-name>/main/docs/latest.json
  ```
- **Усі доступні дати (історія до 14 днів):**
  ```text
  https://raw.githubusercontent.com/<username>/<repo-name>/main/docs/outages.json
  ```

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
