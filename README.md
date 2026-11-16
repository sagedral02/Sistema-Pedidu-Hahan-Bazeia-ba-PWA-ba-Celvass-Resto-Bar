<div align="center">

# 🍽️ CELVAS-PWA

### Sistema Pedidu Hahan Bazeia ba PWA ba Celvass Resto & Bar

### *(Progressive Web App-Based Food and Beverage Ordering System for Celvass Resto & Bar)*

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2%20LTS-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![Django REST Framework](https://img.shields.io/badge/DRF-3.18-red?logo=django&logoColor=white)](https://www.django-rest-framework.org/)
[![Channels & WebSockets](https://img.shields.io/badge/Channels-4.3%20%2B%20Redis-blue)](https://channels.readthedocs.io/)
[![PWA](https://img.shields.io/badge/PWA-Offline%20%2B%20Service%20Worker-5A0FC8?logo=pwa&logoColor=white)](https://web.dev/progressive-web-apps/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Neon](https://img.shields.io/badge/Neon-PostgreSQL-00E599?logo=postgresql&logoColor=white)](https://console.neon.tech)
[![Vercel Deployment](https://img.shields.io/badge/Vercel-celvass--pwa.vercel.app-black?logo=vercel&logoColor=white)](https://celvass-pwa.vercel.app)
[![Versaun](https://img.shields.io/badge/Versaun-2.1.0-blue)](https://github.com/sagedral02/Sistema-Pedidu-Hahan-Bazeia-ba-PWA-ba-Celvass-Resto-Bar)
![Lisensa](https://img.shields.io/badge/Lisensa-MIT-green)

*Pedidu Lalais. Servisu Di'ak. Esperiénsia Modernu.*

</div>

---

## 📖 Kona-ba Celvass Resto & Bar PWA

**Celvass Resto & Bar PWA** mak sistema dijitál ba pedidu hahan no hemu (*dine-in ordering system*) bazeia ba **Progressive Web Application (PWA)** ne'ebé dezenvolve espesifikamente ba **Celvass Resto & Bar** (Av. de Portugal, Praia dos Coqueiros, Dili, Timor-Leste).

Sistema ne'e fasilita kliente sira atu scan QR Code dinámiku iha kada meja, haree lista menu ho lian haat (Tetun, Português, English, no Bahasa Indonesia), hili variante hahan, no haruka pedidu direta husi smartphone la presiza download aplikasaun husi Play Store ka App Store.

Plataforma ne'e integra **Cashier Verification Gate** (*gerbang verifikasi kasir*) ne'ebé estritu atu asegura katak pedidu hotu tenke hetan validasaun husi kasir antes tama ba koziña (KDS), prevene pedidu falsu ka spam, jestaun split-bill no troka osan iha moeda Dólar Amerikanu (**USD $**), no sinkronizasaun dadus *real-time* liu husi WebSockets.

---

## 🚀 Kapasidade Prinsipál

| Área | Kapasidade |
|---|---|
| 📱 Kliénte & PWA | Scan QR meja, menu interativu, offline catalog caching, sesta/cart, multi-idioma (Tetun, PT, EN, ID), instalavel (A2HS) |
| 🛡️ Cashier Gate | Verifikasaun pedidu foun antes tama koziña, aprova/rejeita ho razaun, jestaun sesaun meja |
| 🍳 Koziña (KDS) | Painél Kitchen Display System real-time, audio alert bainhira pedidu tama, tranzisaun estatutu (`PREPARING` ➔ `READY` ➔ `SERVED`) |
| 💵 Kaixa & Pagamentu | Pagamentu Cash (kalkulasaun automatiku osan simu & troka), Card/QR dijitál, emisaun resibu no invoice print |
| 📋 Menu & Katálogu | Kategoria, menu item, jestaun disponibilidade (*in-stock* / *out-of-stock*), presu autoritativu 100% husi servidór |
| 🪑 Meja & Sesaun | QR token seguru kada meja, kontrolu sesaun ativu, limitasaun pedidu pendente ba seguransa husi spam |
| 📡 Real-Time & Event | WebSockets (Django Channels + Redis) ba atualizasaun instantáneu entre Kliénte, Kasir, no Koziña sem refresh |
| 📨 Transactional Outbox | Outbox pattern ba fiabilidade mensajen no event dispatch sem lakon dadus transasaun |
| 🔐 Seguransa & RBAC | Role-Based Access (Admin, Cashier, Kitchen), device lock ho cookie httpOnly, CSRF, Idempotency-Key ba prevensaun double-order |
| 📊 Auditoria & Relatóriu | Audit trail kompletu ba kada mudansa estatutu no movimentu osan, relatóriu vendas diáriu no analítika menu |
| 🐳 Operasaun & Infra | Docker Compose (Daphne ASGI + Redis + Postgres + Nginx), SQLite fallback ba ambiente dezenvolvimentu |

---

## 🏗️ Arkitetura Badak

```text
Kliénte (Mobile PWA / Scan QR Meja)
             │
             ▼
        [REST API] ──► Idempotency-Key + Validasaun Presu (Server-Authoritative)
             │
             ▼
   [Estatutu: SUBMITTED]
             │
             ▼
  ┌─────────────────────────┐
  │  Cashier Gate (Kaixa)   │ ──► [Rejeita] ──► Notifika Kliénte
  └──────────┬──────────────┘
             │ (Aprova / Confirma)
             ▼
   [Estatutu: CONFIRMED]
             │
             ├──► [Transactional Outbox] ──► WebSocket Broadcast (Redis)
             │
             ▼
   ┌───────────────────────┐
   │ Koziña KDS Painél     │ ──► Audio Chime Alert + Live Ticket
   └──────────┬────────────┘
              │ (PREPARING ──► READY ──► SERVED)
              ▼
      Kaixa / Pagamentu (Cash / Digital USD $)
              │
              ▼
   [Estatutu: COMPLETED] ──► Imprime Resibu & Audit Trail
```

---

## 🛠️ Tech Stack

| Kategoria | Teknolojia |
|---|---|
| Linguajen | Python `3.11+`, JavaScript (ES6+ Vanilla PWA) |
| Backend Framework | Django `5.2 LTS`, Django REST Framework `3.18` |
| Real-time & Asínkrona | Django Channels `4.3`, Daphne ASGI `4.2`, Redis `7` |
| Frontend & PWA | HTML5 Semántiku, CSS3 Modernu, Service Worker, Web App Manifest |
| Baze de Dadus | PostgreSQL `16+` (Production), SQLite3 (Development / Testing) |
| Event & Concurrency | Celery `5.6`, Transactional Outbox Pattern |
| Seguransa & Auth | Custom RBAC (4 Roles), Session Auth, Idempotency-Key, CSRF |
| Operasaun & Deploy | Docker Compose, Nginx, Daphne, WhiteNoise, Pytest |

---

## ⚙️ Instalasaun Lalais

### Pre-rekizitu

- Python `3.11+` no `pip`;
- Git;
- Redis (ba ambiente WebSockets / Channels);
- PostgreSQL `16+` (ba produsaun) ka SQLite3 (ba dezenvolvimentu lokál).

### 1. Klonen Repozitóriu

```bash
git clone https://github.com/sagedral02/Sistema-Pedidu-Hahan-Bazeia-ba-PWA-ba-Celvass-Resto-Bar.git
cd Sistema-Pedidu-Hahan-Bazeia-ba-PWA-ba-Celvass-Resto-Bar
```

### 2. Ambiente Virtuál & Dependénsia

```bash
# Kria no ativa ambiente virtuál
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Linux / macOS:
source .venv/bin/activate

# Instala package sira
pip install -r requirements/dev.txt
```

### 3. Konfigura Variável Ambiente (.env)

```bash
cp .env.example .env
```

Ajusta valór iha `.env` tuir nesesidade lokál ka produsaun:

```env
DJANGO_SETTINGS_MODULE=config.settings.local
SECRET_KEY=sua-chave-secreta-lokal-12345
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
DATABASE_URL=sqlite:///db.sqlite3
DEFAULT_CURRENCY=USD
TIME_ZONE=Asia/Dili
RESTAURANT_NAME=Celvass Resto & Bar
```

### 4. Migrasaun & Seed Demo Data

Exekuta migrasaun baze de dadus no komandu `seed_demo` atu kria perfil restorante, konta utilizadór, mejas, kategoria, no menu sira:

```bash
python manage.py migrate
python manage.py seed_demo
```

### 5. Halai Servidór

Akompanha ho Daphne ASGI atu suporta funsionalidade WebSockets:

```bash
# Halai ho Daphne ASGI (WebSockets ativu):
daphne -b 127.0.0.1 -p 8000 config.asgi:application

# Ka uza Django runserver ba teste simples:
python manage.py runserver
```

---

## 👥 Konta Default Demo

Depois de halo `seed_demo`, konta sira tuir mai bele uza direta:

| Role | Username | Password | URL Portal | Funsionál Prinsipál |
|---|---|---|---|---|
| **Admin** | `admin` | `admin123` | `/admin-portal/` | Jestaun menu, relatóriu vendas, no QR print sheet |
| **Cashier (Kaixa)** | `cashier` | `cashier123` | `/cashier/` | Verifika pedidu foun, simu pagamentu, no split-bill |
| **Kitchen (Koziña)** | `kitchen` | `kitchen123` | `/kitchen/` | Painél KDS, audio alert, atualiza estatutu preparasaun |
| **Customer (Kliente)** | *(Sem Login)* | *(Scan QR)* | `/t/demo-table-1/` | Hili menu, sesta, no submete pedidu dine-in |

---

## 📱 Fitur PWA (Progressive Web App)

- **Offline Capability**: Service Worker (`static/js/sw.js`, servidu iha `/sw.js`) rai CSS, JS, font no ikone (self-hosted iha `static/vendor/`). Pájina kliente (`/`, `/t/...`) uza *network-first* no copy cache foin uza bainhira offline. Portal staff, login/logout no API **nunka** rai iha cache; logout hamoos cache browser (`Clear-Site-Data`).
- **Web App Manifest (`/manifest.json`)**: Instalavel iha *Home Screen* (standalone), orientasaun livre (`any`) atu KDS no Kaixa bele uza tablet landscape.
- **Device lock**: Servidór haruka cookie `celvass_device_id` (httpOnly) bainhira loke pájina meza. Sesaun ne'ebé iha ona na'in rejeita telemóvel seluk, inklui pedidu sem cookie.
- **Multi-Idioma Dinámiku (`i18n.js`)**: Kliente bele muda lian direta iha menu entre **Tetun**, **Português**, **English**, no **Bahasa Indonesia**.
- **Real-Time Sound System**: Notifikasaun sonoru automatiku iha portal Kaixa no Koziña bainhira pedidu foun tama ka prontu ona.

---

## 🐳 Docker

Subi ambiente produsaun kompletu (PostgreSQL, Redis, Daphne Web App, no Nginx) ho komandu simples:

```bash
docker compose up -d --build
docker compose ps
```

Parar servisu:

```bash
docker compose down
```

---

## 🧪 Dezenvolvimentu & Testes

Projetu ne'e kobre ho teste automatizadu rigorozu ba regra negósiu, seguransa osan, no tranzisaun estatutu:

```bash
pytest -v
```

Teste sira kobre:
1. `test_api_endpoints.py` — Verifikasaun REST API meja, menu, no submete pedidu.
2. `test_cashier_gate.py` — Garante pedidu labele tama koziña sem konfirmasaun kasir.
3. `test_idempotency_and_limits.py` — Prevensaun pedidu duplikadu no limitasaun spam.
4. `test_pricing_and_money.py` — Validasaun kalkulasaun osan, troka, no presu autoritativu.
5. `test_state_machines.py` — Tranzisaun estatutu pedidu no sesaun meja.
6. `test_security_hardening.py` — Device lock, tracker so ba meza rasik, validasaun `guest_count`, erro 500 la hatudu detallu internu, KPI admin, redirect login tuir papél.
7. `test_ui_shell_and_pwa.py` — Navegasaun tuir papél, chave i18n kompletu iha lian 4, la iha CDN / emoji / class Bootstrap la válidu, manifest, service worker, logout no password staff.

---

## 🎨 Interface (v3)

- Tema naroman (fundu `#faf8f5`, superfísie mutin), kór aksentu tuir portal: Kliente `#c2410c`, Kaixa `#047857`, Dapur `#1d4ed8`, Admin `#334155`. Font ida de'it: Plus Jakarta Sans.
- Telemóvel (< 768px): tab bar iha kraik (maksimu 5, seluk iha "Seluk"). Tablet/laptop: navegasaun horizontál iha top bar. Panel uza hash URL (ez. `/admin-portal/#menu-panel`).
- Tabela sai kartaun iha telemóvel (`.table-stack` + `data-label`).
- Arkivu prinsipál: `static/css/style.css`, `static/js/app.js` (panel, dialog konfirma, escape HTML), `templates/base/_nav_*.html` no `_tabbar_*.html`.
- Depois muda CSS/JS, halai `python manage.py collectstatic --noinput` tanba Vercel servi `staticfiles/`.

---

## 📚 Dokumentasaun

| Dokumentu | Konteúdu |
|---|---|
| [`prd.md`](prd.md) | *Product Requirements Document* kompletu (PRD-RESTO-PWA-001 v2.1.0) |
| [`docker-compose.yml`](docker-compose.yml) | Konfigurasaun multi-kontentór Docker ba produsaun |
| [`requirements/`](requirements/) | Dependénsia Python tuir ambiente (`base.txt`, `dev.txt`, `prod.txt`) |

---

## ⚠️ Nota Production

- **Baze de Dadus**: Nunka uza SQLite iha produsaun; uza sempre PostgreSQL `16+`.
- **Daphne ASGI**: Oportunidade real-time depende ba Daphne no Redis; labele uza `python manage.py runserver` iha produsaun.
- **Seguransa Presu**: Presu hahan nian foti 100% husi baze de dadus servidór; sistema rejeita kalkulasaun presu ne'ebé haruka husi browser kliente.
- **HTTPS & WSS**: Ba produsaun tenke ativa sertifikadu SSL/TLS liu husi Nginx Reverse Proxy atu proteje tráfiku WebSockets no autoriza Service Worker PWA.

---

## 📜 Lisensa

Projetu ida-ne'e uza lisensa **MIT**.

<div align="center">

Harii ho ❤️ ba modernizasaun no dezenvolvimentu dijitál F&B iha Timor-Leste.

**Celvass Resto & Bar** — *Pedidu Lalais. Servisu Di'ak. Esperiénsia Modernu.*

</div>
