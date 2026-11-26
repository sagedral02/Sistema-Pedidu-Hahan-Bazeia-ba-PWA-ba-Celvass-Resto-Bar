<div align="center">

# 🍽️ CELVASS PWA

### Sistema Pedidu Hahan Bazeia ba PWA ba Celvass Resto & Bar

*(Progressive Web App-Based Food and Beverage Ordering System for Celvass Resto & Bar)*

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2%20LTS-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.18-red)](https://www.django-rest-framework.org/)
[![Channels](https://img.shields.io/badge/Channels-4.3-blue)](https://channels.readthedocs.io/)
[![PWA](https://img.shields.io/badge/PWA-Web%20Push%20%2B%20Offline-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)
[![Vercel](https://img.shields.io/badge/Vercel-celvass--pwa.vercel.app-black?logo=vercel&logoColor=white)](https://celvass-pwa.vercel.app)
[![Versaun](https://img.shields.io/badge/Versaun-3.0.0-blue)](prd.md)
![Lisensa](https://img.shields.io/badge/Lisensa-MIT-green)

</div>

---

## 📖 Kona-ba projetu

**Celvass PWA** mak aplikasaun web ne'ebé bele instala (PWA) ba restorante **Celvass Resto & Bar** (Praia dos Coqueiros, Dili). Kliente haree menu iha pájina prinsipál, skanu QR iha meza, no halo pedidu husi sira-nia telemóvel rasik la presiza konta. Kaixa verifika pedidu molok tama dapur, simu pagamentu osan-mean (USD), no jere meza. Dapur haree fila tuir item. Admin jere menu, kuota, meza, staff, konfigurasaun restorante, no relatóriu.

Lian 4: **Tetun** (default), **Português**, **English**, **Bahasa Indonesia**.

Dokumentu kompletu ba regra negósiu, state machine, API no arkitetura: **[`prd.md`](prd.md)**.

---

## 🚀 Funsionalidade tuir portal

| Portal | Saida mak iha |
|---|---|
| **Pájina prinsipál `/`** | Logo, deskrisaun, status loke/taka no oras ohin, butaun **Skanu QR meza**, menu atu haree de'it (kategoria, buka, rating, pedidu barak liu, rekomendasaun), kontaktu (WhatsApp, telefone, email, website, sosiál), mapa Google Maps no oras loke semana nian |
| **Kliente `/t/<qr>/`** | Hatama ema nain hira (tuir kapasidade meza) → husu loke meza → katálogu ho karreta → tuir pedidu tuir item → husu konta → pájina obrigadu ho rating ba hahan ida-idak. Telemóvel seluk haree pájina **Meza okupadu** (la iha reload la para). Butaun **Ativa notifikasaun** (Web Push). |
| **Kaixa `/cashier/`** | **POS**: fila (pedidu loke meza + pedidu hein), kartaun meza (Loke sesaun → Pedidu → Request bill + **+**), detalle pedidu ho status item, take away. **Atendimentu**: Konta (selu iha ne'e de'it), Info dapur, **Meza la iha atividade**. **Menu & Stok**, **Turnu** (kaixa loke/taka), **Relatóriu**. Gembok: *Taka normál* ka *Bainaka sai la selu* (ho razaun, rejista lakon). |
| **Dapur `/kitchen/`** | Fila tuir item (hein / tein / prontu), aksaun ba item ka pedidu tomak, marka **la iha** ho razaun |
| **Admin `/admin-portal/`** | Rezumu (KPI + gráfiku: vendas, vizitante, la selu…), Katálogu (menu, kuota inline, rating no review), Meza & QR, Staff, **Konfigurasaun** (logo = ikone PWA, kontaktu, mapa, oras loke, taxa, nota rekibu, minutu alerta meza la iha atividade), Relatóriu |
| **Relatóriu `/reports/`** | Rezumu (vendas loron-loron, menu, tipu pedidu, kaixa, rekuza/kansela/anula/sai la selu, uza meza, turnu, vizitante), Istóriku (pedidu + transasaun), Audit. Esporta CSV no PDF. |

---

## 🏗️ Arkitetura

```text
Browser / PWA (vanilla JS + Bootstrap, service worker v11, Web Push, skaner QR)
      │ HTTPS REST (/api/v1/)          │ WSS (/ws/)
      ▼                                ▼
Django 5.2 + DRF  ──  service layer (transaction.atomic + select_for_update)
      │                     └─ Outbox ─► WebSocket (Channels) + Web Push (pywebpush)
      ▼
PostgreSQL (produsaun) / SQLite (lokál)  ·  Redis (channel layer produsaun)
```

| App | Responsabilidade |
|---|---|
| `accounts` | Utilizadór ho papél ADMIN / CASHIER / KITCHEN |
| `restaurants` | Restorante, oras loke, logo/ikone, pájina kliente no portal |
| `tables` | Meza, sesaun, device lock, pedidu loke meza, take away, progresu kartaun, *walkout*, meza la iha atividade |
| `catalog` | Kategoria, menu, kuota loron-loron, taka ohin, rating, highlight |
| `ordering` / `kitchen` | Pedidu, status tuir item, info dapur |
| `payments` / `shifts` | Konta, pagamentu osan-mean, rekibu, turnu kaixa |
| `reporting` / `audit` | Rezumu, relatóriu, istóriku, esporta, audit log |
| `outbox` / `realtime` / `pwa` | Event, WebSocket, manifest, service worker, ikone, Web Push |

---

## ⚙️ Instalasaun lokál

Pre-rekizitu: Python 3.12, Git. (Redis no PostgreSQL ba produsaun de'it.)

```bash
git clone https://github.com/sagedral02/Sistema-Pedidu-Hahan-Bazeia-ba-PWA-ba-Celvass-Resto-Bar.git
cd Sistema-Pedidu-Hahan-Bazeia-ba-PWA-ba-Celvass-Resto-Bar

python -m venv .venv
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

pip install -r requirements/dev.txt
copy .env.example .env        # Linux/macOS: cp .env.example .env

python manage.py migrate
python manage.py seed_demo              # restorante, staff, meza, menu demo
python manage.py generate_brand_icons   # ikone default (garfu no faka)
python manage.py runserver 127.0.0.1:8000
```

WebSocket ho Daphne: `daphne -b 127.0.0.1 -p 8000 config.asgi:application`.

### Variável ambiente

| Variável | Uza ba |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, `REDIS_URL` | Django no baze de dadus |
| `CUSTOMER_HOME_URL` | Fatin kliente ba depois pájina obrigadu (ez. `https://celvass-pwa.vercel.app/`) |
| `BUSINESS_DAY_START_HOUR` | Oras loron negósiu hahú (kuota no numeru take away reset) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Web Push. Kria ho `python manage.py generate_vapid_keys`. Mamuk = push desliga. |
| `PUSH_ASYNC` | `1` (default) haruka push iha thread seluk |

### Komandu útil

| Komandu | Funsaun |
|---|---|
| `python manage.py seed_demo` | Dadus demo |
| `python manage.py generate_brand_icons` | Kria ikone PWA default |
| `python manage.py generate_vapid_keys` | Kria xave Web Push |
| `python manage.py check_idle_tables` | Haruka alerta meza la iha atividade (uza iha cron, ez. kada minutu 5) |
| `python manage.py collectstatic --noinput` | Prepara static ba Vercel / produsaun |

---

## 👥 Konta demo (depois `seed_demo`)

| Papél | Username | Password | Portal |
|---|---|---|---|
| Admin | `admin` | `admin123` | `/admin-portal/` |
| Kaixa | `cashier` | `cashier123` | `/cashier/` |
| Dapur | `kitchen` | `kitchen123` | `/kitchen/` |
| Kliente | *(la presiza)* | *(skanu QR)* | `/` ka `/t/<qr_token>/` |

> Troka password hotu molok uza iha produsaun.

---

## 📱 PWA

- **Instala** iha Home Screen (manifest `/manifest.json`, ikone husi logo restorante ka garfu-faka default).
- **Service worker** (`/sw.js`, cache `v11`): aset static rai iha cache, pájina kliente *network-first*. Portal staff, login no API nunka rai iha cache.
- **Web Push**: kaixa simu pedidu foun / husu konta / meza la iha atividade; dapur simu pedidu foun; kliente simu meza loke, pedidu konfirma, hahan prontu, obrigadu. Permisaun husu de'it bainhira prima butaun. iPhone: tenke instala PWA uluk (iOS 16.4+).
- **Skaner QR** iha aplikasaun (kamera), aseita de'it link meza husi site ne'e rasik.
- Vendor hotu (Bootstrap, Font Awesome, font, jsQR) iha `static/vendor/` (la uza CDN).

---

## 🧪 Teste

```bash
# Teste funsionál (la presiza browser)
python -m pytest -m "not browser" -q

# Teste browser (Playwright, tests/ui/)
python -m pytest -m browser

# Verifika migrasaun
python manage.py makemigrations --check --settings=config.settings.test
```

Teste kobre regra negósiu (cashier gate, device lock, kapasidade meza, oras loke, kuota, status item, konta, osan-mean, turnu, *walkout*, meza la iha atividade), API no permisaun, relatóriu no esporta, label lian 4, template, service worker no Web Push (ho mock).

---

## 🚢 Deploy

| Ambiente | Oinsá |
|---|---|
| **Docker** | `docker compose up -d --build` (PostgreSQL, Redis, Daphne, Nginx) |
| **Vercel** | `vercel.json` (WSGI). Halai `collectstatic` depois muda CSS/JS. Baze de dadus Neon PostgreSQL. Logo/ikone rai iha baze de dadus (disk Vercel read-only). |

Nota produsaun:

- Uza PostgreSQL, la'ós SQLite.
- HTTPS obrigatóriu (service worker, kamera, Web Push).
- Rai xave VAPID privadu no `SECRET_KEY` iha variável ambiente de'it.
- Presu no totál kalkula iha servidór de'it.

---

## 📚 Dokumentasaun

| Dokumentu | Konteúdu |
|---|---|
| [`prd.md`](prd.md) | Product Requirements Document v3.0.0 (sistema atuál) |
| [`.env.example`](.env.example) | Ezemplu variável ambiente |
| [`docker-compose.yml`](docker-compose.yml) | Ambiente Docker |
| [`requirements/`](requirements/) | Dependénsia (`base.txt`, `dev.txt`, `prod.txt`) |

---

## 📜 Lisensa

MIT.

<div align="center">

**Celvass Resto & Bar** · *Pedidu Lalais. Servisu Di'ak. Esperiénsia Modernu.*

</div>
