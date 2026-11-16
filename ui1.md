> **Nota (v3, Novembru 2026):** Dokumentu ne'e istóriku. Tema "dark glassmorphism" no "flying glass dock" troka ona ho tema naroman, top bar + tab bar iha telemóvel, no tabela ne'ebé sai kartaun iha ekran ki'ik. Haree seksaun "Interface (v3)" iha `README.md` no `static/css/style.css` ba padraun atuál.

# CELVASS RESTO & BAR — SPECIFIKASAUN TÉKNIKA REVISI V2.2 (UI1.MD)
## AUDIT IMPLEMENTASAUN UI.MD, KOMPAKTIKASAUN KATALOGU MENU DINÁMIKU & SISTEMA MACOS-STYLE FLYING GLASS DOCK MULTI-ROLE

> **Document Status**: Production Engineering Blueprint & Implementation Masterplan v2.2  
> **Target Version**: v2.2.0 Adaptive Typography, Compact Mobile Food Cards & macOS Flying Glass Dock  
> **Audience**: Junior Developers, Low-Cost AI Coding Agents, Frontend Engineers, QA Automation Testers  
> **Design Language**: Ultra-Luxury Dark Obsidian Glassmorphism (`#090500` obsidian mesh, 24px-32px backdrop blur, radiant amber-orange `#f97316`, macOS-inspired floating pill docks)  
> **Target Devices**: Mobile Smartphones (360px – 430px), Tablets / iPads (768px – 1024px), Laptops & Desktop Monitores (> 1024px)  
> **Target Jurisdiction**: Dili, Timor-Leste (Base Language: Tetun Prasa)  

---

## ÍNDISE / TABLE OF CONTENTS
1. [Rezumu Esekutivu & Objetivu Prinsipál Revisaun v2.2](#1-rezumu-esekutivu--objetivu-prinsipál-revisaun-v22)
2. [Audit Komprehensivu Implementasaun UI.MD (Gap Analysis: Realidade vs Teoria)](#2-audit-komprehensivu-implementasaun-uimd-gap-analysis-realidade-vs-teoria)
3. [Analiza Problema Tékniku & Root Cause: Kartaun Hahán Mobile la Mosu Naran](#3-analiza-problema-tékniku--root-cause-kartaun-hahán-mobile-la-mosu-naran)
4. [Solusaun Arquitetura Kartaun Hahán: Compact, Dinámiku, & Interaktivu](#4-solusaun-arquitetura-kartaun-hahán-compact-dinámiku--interaktivu)
5. [Paradigma Foun: macOS-Style "Flying Glass Dock" ba Kargu Hotu (All Roles)](#5-paradigma-foun-macos-style-flying-glass-dock-ba-kargu-hotu-all-roles)
   - 5.1. [Anatomia & Prinsípiu Flying Glass Dock](#51-anatomia--prinsípiu-flying-glass-dock)
   - 5.2. [Customer Flying Dock (Mobile Bottom vs Tablet/Desktop Floating Header)](#52-customer-flying-dock-mobile-bottom-vs-tabletdesktop-floating-header)
   - 5.3. [Admin Flying Dock (Kartaun Jestaun Dedikadu, Hamoos Link Kaixa/Dapur)](#53-admin-flying-dock-kartaun-jestaun-dedikadu-hamoos-link-kaixadapur)
   - 5.4. [Cashier POS Flying Dock (Sentru Kontrolu Kaixa & Atalho Lais)](#54-cashier-pos-flying-dock-sentru-kontrolu-kaixa--atalho-lais)
   - 5.5. [Kitchen KDS Flying Dock (Painél Dapur, Audio Bell, & Fullscreen)](#55-kitchen-kds-flying-dock-painél-dapur-audio-bell--fullscreen)
6. [Arsitektura Tipografia Dinámika (Fluid Responsive Typography ho CSS Clamp)](#6-arsitektura-tipografia-dinámika-fluid-responsive-typography-ho-css-clamp)
7. [Cetak Biru Kode Implementasi Kompletu (File-by-File Technical Code Blueprint)](#7-cetak-biru-kode-implementasi-kompletu-file-by-file-technical-code-blueprint)
   - 7.1. [Modifikasaun `static/css/style.css` (Flying Dock & Dynamic Card Engine)](#71-modifikasaun-staticcssstylecss-flying-dock--dynamic-card-engine)
   - 7.2. [Modifikasaun `templates/base/base.html` (Unified Flying Dock Renderer)](#72-modifikasaun-templatesbasebasehtml-unified-flying-dock-renderer)
   - 7.3. [Modifikasaun `templates/customer/index.html` (Compact Interactive Cards)](#73-modifikasaun-templatescustomerindexhtml-compact-interactive-cards)
   - 7.4. [Modifikasaun `templates/admin_custom/dashboard.html` (Dedicated Admin Dock)](#74-modifikasaun-templatesadmin_customdashboardhtml-dedicated-admin-dock)
   - 7.5. [Modifikasaun `templates/cashier/dashboard.html` (Cashier Flying Dock)](#75-modifikasaun-templatescashierdashboardhtml-cashier-flying-dock)
   - 7.6. [Modifikasaun `templates/kitchen/kds.html` (Kitchen Flying Dock)](#76-modifikasaun-templateskitchenkdshtml-kitchen-flying-dock)
   - 7.7. [Modifikasaun Javascript Controllers (`customer.js`, `cashier.js`, `kitchen.js`)](#77-modifikasaun-javascript-controllers-customerjs-cashierjs-kitchenjs)
8. [Matriks Verifikasaun & Panduan QA Testing Multi-Device](#8-matriks-verifikasaun--panduan-qa-testing-multi-device)
9. [Instruksaun ba Junior Developer & Low-Cost AI Agents (Execution Guide)](#9-instruksaun-ba-junior-developer--low-cost-ai-agents-execution-guide)

---

## 1. REZUMU ESEKUTIVU & OBJETIVU PRINSIPÁL REVISAUN V2.2

Dokumentu **`ui1.md`** ne'e sai hanesan matadalan revistu husi `ui.md` hodi responde diretamente ba feedback utilizadór relasiona ho implementasaun direta iha produsaun [https://celvass-pwa.vercel.app/](https://celvass-pwa.vercel.app/).

Maski estrutura baze responsivu (bottom navigation dock no 2-koluna grid) instala ona, utilizadór identifika kestaun ergonomia no vizuál krítiku tolu (3):
1. **Naran Menu & Deskrisaun La Mosu / Korta iha Telemóvel (Mobile Food Cards Truncation)**:
   Iha ekran telemóvel ki'ik (< 768px), kartaun hahán ne'ebé fahe ba koluna 2 (luan ~165px kada kartaun) hetan kolizaun layout tanba badge kategoria tau hamutuk iha liña ida ho titulu menu. Rezultadu mak naran menu sai mamuk, lakon, ka hetan korta maka'as to'o letra 3-4 de'it, enkuantu deskrisaun lakon totalmente lahó opsaun atu lee.
2. **Tag Kategoria Inerte (La Bele Hanehan / Non-Interactive)**:
   Tag kategoria iha kartaun hahán funsiona de'it hanesan testu estátiku. La bele hanehan hodi fihir kategoria ne'e ka hodi loke detallu kompletu hahán nian. Kartaun tomak la bele hanehan (so butaun ki'ik `+ Aumenta` de'it mak bele hanehan), halo difisil tebes ba kliente atu haree foto boot no deskrisaun kompletu.
3. **Header Navbar Antigu & Presiza macOS-Style "Flying Glass Dock" ba Kargu Hotu**:
   - Header atual iha parte leten sei uza navbar tradisionál ne'ebé okupa espasu ekran vertikál no la refleta estétika moderna.
   - Iha portal **Admin**, header leten iha link direta ba Kaixa no Dapur ne'ebé loloos la presiza, tanba admin iha knaar jestaun relatóriu no konfigurasaun, la'os operasionál direta.
   - Utilizadór ezije atu refactor navegasaun tomak ba modelu **Flying Dock (Dock Melayang hanesan macOS)** ho estétika ultra-luxury dark glassmorphism, aplikavel ba kargu hotu: **Customer**, **Admin**, **Cashier (Kaixa)**, no **Kitchen (Dapur)**, tantu iha laptop, tablet, no telemóvel.

---

## 2. AUDIT KOMPREHENSIVU IMPLEMENTASAUN UI.MD (GAP ANALYSIS)

Tabela tuir mai hatudu komparasaun entre saida mak planea iha `ui.md`, saida mak eziste atualmente iha kódigu, no defisiénsia ne'ebé tenke hadi'a iha `ui1.md`:

| Aspeku UI / Komponen | Planeamentu iha `ui.md` | Kondisaun Atual iha Kódigu | Status & Kesenjangan (Gap) | Asaun Corretiva iha `ui1.md` |
| :--- | :--- | :--- | :--- | :--- |
| **Mobile Bottom Dock** | 5-Tab fiksa iha kraik ho safe-area | Implementa ona iha `base.html` | ✅ Sucesso ba Mobile, maibé ekran tablet/desktop sei uza navbar tradisionál | Hadi'a ekran boot atu uza **macOS Top Flying Dock** |
| **Kartaun Menu Mobile** | 2-Kolom kompak ho foto 135px | Implementa ona iha `style.css` | ⚠️ **FAIL CRITICAL**: Naran menu la mosu tanba tag kategoria fahe liña hanesan ho titulu iha luan 165px | Muda tag kategoria sai **Floating Chip** iha leten foto; titulu foti luan 100% |
| **Deskrisaun Menu Mobile** | Subar iha mobile ki'ik | `display: none !important;` | ⚠️ **UX POOR**: Kliente la bele hatene hahán ne'e nia sabor ka ingredientes | Uza **1-line fluid micro-desc** ho `clamp()` no permiti klik kartaun atu loke modal detallu |
| **Interatividade Kartaun** | Butaun `+` ki'ik de'it | So butaun `+` mak bele klik | ⚠️ **POOR TOUCH**: Foto no titulu la reajiste ba klik/tap | Kartaun tomak (foto, titulu, badge) sai **clickable surface** ba `openItemModal` |
| **Header Admin Portal** | Navbar tradisionál | Sei hatudu link Kaixa & Dapur | ⚠️ **IRRELEVANT LINKS**: Admin la presiza link operasionál kaixa/dapur iha header | Refactor sai **macOS Admin Flying Dock** ho 5 tab jestaun sentral |
| **Header Cashier & POS** | Nav pills iha leten dashboard | Nav pills tradisionál | ⚠️ **INCONSISTENT**: La'os floating dock, forma estátika | Transforma ba **macOS Cashier Flying Dock** |
| **Header Kitchen KDS** | Segmented tabs iha mobile, header estátiku | Segmented tabs funsiona | ⚠️ **INCONSISTENT**: Header leten sei estátiku | Transforma ba **macOS Kitchen Flying Dock** |
| **Tipografia Responsivu** | Pixels fiksa (`0.88rem`, `1.05rem`) | Pixels fiksa | ⚠️ **RIGID TEXT**: La fluid tuir luan ekran | Aplika **CSS `clamp()` Engine** ba textu hotu |

---

## 3. ANALIZA PROBLEMA TÉKNIKU & ROOT CAUSE: KARTAUN HAHÁN MOBILE LA MOSU NARAN

### 3.1. Sintoma (Defect Phenomenon)
Bainhira utilizadór loke menukatalogu iha telemóvel (ezemplu iPhone 14 Pro luan 393px ka Samsung Galaxy luan 360px-412px):
1. Foto hahán mosu iha altura 135px.
2. Iha parte body kartaun, naran hahán (menu name) **la mosu, lakon, ka mosu de'it pontu-pontu (`...`)**.
3. Deskrisaun hahán lakon 100%.
4. Presu no butaun `+` de'it mak mosu iha kraik.

### 3.2. Root Cause Analysis (Analiza Kauza Raíz)
Iha `templates/customer/index.html` liña 114–117, estrutura HTML mak tuir mai:
```html
<div class="d-flex justify-content-between align-items-start mb-1">
  <h6 class="menu-card-title">{{ item.name }}</h6>
  <span class="badge bg-white bg-opacity-10 border border-white border-opacity-10 text-white-50 small ms-1">{{ cat.name }}</span>
</div>
```
No iha `static/css/style.css`:
```css
@media (max-width: 767.98px) {
  #menu-grid .menu-item-col {
    width: 50% !important;
  }
}
```

Bainhira ekran telemóvel iha luan `375px`:
- Luan container pedidu = `375px - padding (24px) = 351px`.
- Kada koluna fahe 50% = `175.5px`.
- Kartaun iha padding lateral `0.75rem` (12px kada sorin) -> Luan efetivu kartaun nia laran = `175.5px - 24px = 151.5px`!
- Iha luan **151.5px**, elementu rua ne'e fahe espasu liuhosi `d-flex justify-content-between`:
  1. `<span class="badge ...">{{ cat.name }}</span>`: Kategoria hanesan "Bebidas & Sumu" ka "Pratu Prinsipál" foti luan **85px to'o 110px**!
  2. Espasu ne'ebé hela ba `<h6 class="menu-card-title">` mak de'it **40px to'o 60px**!
- Ho luan ki'ik ne'e, no CSS regra `-webkit-line-clamp: 2; overflow: hidden;`, naran menu hanesan "Ikan Saboko Fresku" ka "Cerveza Heineken Cold" foti letra 3-4 de'it hafoin korta sai `Ika...` ka lakon tanba line-height overflow!

---

## 4. SOLUSAUN ARQUITETURA KARTAUN HAHÁN: COMPACT, DINÁMIKU, & INTERAKTIVU

### 4.1. Repozisionamentu Tag Kategoria: "Floating Glass Category Chip"
Tag kategoria la bele sulan tan iha liña titulu. Tag kategoria sei muda ba **Floating Glass Chip** ne'ebé melayang iha leten foto hahán (Top-Right ka Top-Left) ho estétika glassmorphic suave.
* **Vantajen**: Titulu menu hetan luan **100% husi kartaun (151px)**, halo titulu bele lee ho fasil to'o liña 2 kompletu lahó korta.
* **Interatividade**: Bainhira kliente hanehan tag kategoria ne'e, sistema sei filtra kedas katalogu ba kategoria ne'e ho animasaun lais!

### 4.2. Kartaun Tomak Sai Clickable Surface
* La'os de'it butaun `+` mak bele klik. Bainhira kliente hanehan parte ruma husi kartaun (foto, titulu, deskrisaun), sistema sei loke kedas **Item Customization Modal** ho foto boot rezolusaun alta, deskrisaun kompletu, notasaun xefe, no kuantidade.

### 4.3. Micro-Deskrisaun Dinámika
Iha fatin subar totalmente deskrisaun, ita sei hatama **1-liña micro-deskrisaun** ho kór suave (`color: rgba(255,255,255,0.6)`) no letra ki'ik dinámiku (`clamp(0.68rem, 1.8vw, 0.78rem)`).

```
+------------------------------------------+
|  +------------------------------------+  |
|  | [Chef Special]   [Seafood Chip*]   |  | <- Floating Chips on Image
|  |                                    |  |
|  |           FOTO HAHÁN               |  | (135px height, object-fit: cover)
|  |                                    |  |
|  |               [⏱ 10-15m]           |  |
|  +------------------------------------+  |
|                                          |
|  IKAN SABOKO DILI SPECIAL                | <- 100% Width Title (Dynamic Clamp)
|  Ikan fresku tasi Dili ho modo...        | <- 1-Line Micro Description
|                                          |
|  $4.50                      [ + Add ]    | <- Price & Fast Add Button
+------------------------------------------+
  *Klik Chip -> Filtra Kategoria
  *Klik Kartaun/Foto -> Loke Modal Detallu
```

---

## 5. PARADIGMA FOUN: MACOS-STYLE "FLYING GLASS DOCK" BA KARGU HOTU (ALL ROLES)

### 5.1. Anatomia & Prinsípiu Flying Glass Dock
Navegasaun tradisionál ne'ebé belit iha tetun ekran (edge-to-edge navbar) substitui ho **macOS Flying Glass Dock**:
1. **Floating Position**: La belit iha ninin ekran. Iha marjen husi leten/kraik (e.g. `margin: 0.8rem auto`).
2. **Ultra-Luxury Glass Material**: Fundo obsidian nakukun `rgba(12, 17, 28, 0.85)`, borda naroman suave `1px solid rgba(255, 255, 255, 0.12)`, no blur boot `backdrop-filter: blur(28px)`.
3. **Pill-Shaped Geometry**: Forma pílula ka oval arredondadu (`border-radius: 9999px` ka `24px`).
4. **macOS Magnification Hover Effect**: Bainhira cursor mouse hakbesik ba íkone dock, íkone sa'e ba leten ho zoom suave (`transform: translateY(-4px) scale(1.15); transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1)`).
5. **Active Radiant Indicator**: Tab ne'ebé ativu hetan kór gradiente oranje-amber nabilan ho lakan suave (`box-shadow: 0 0 20px var(--primary-glow)`).

---

### 5.2. Customer Flying Dock (Mobile vs Desktop)
* **Iha Mobile (< 768px)**:
  * **Bottom Flying Dock**: Melayang 8px iha kraik tela ho marjen lateral:
    `[ 🍽️ Menu ]  [ 🔍 Buka ]  [ 🛒 Karreta (Badge) ]  [ ⏱️ Status ]  [ 🧾 Konta ]`
  * **Top Compact Floating Pill**: Hatudu naran restorante, meza (`Meza 01`), no fihir lian (`🇹🇱 Tetun`).
* **Iha Tablet & Laptop / Desktop (>= 768px)**:
  * **Top macOS Flying Dock**: Melayang sentralizadu iha leten:
    `[ 🍽️ Celvass Resto | 🪑 Meza 01 | 🔍 Buka Menu | 🛒 Karreta ($0.00) | 🇹🇱 Lian | 🛡️ Staff ]`

---

### 5.3. Admin Flying Dock (Hamoos Link Kaixa & Dapur!)
Utilizadór ho kargu **Admin** la presiza link Kaixa no Dapur iha ninia navegasaun prinsipál. Admin ninia fokus mak operasaun jestaun, finansas, no relatóriu.

**Estrutura macOS Admin Flying Dock**:
```
+---------------------------------------------------------------------------------------------------------+
| [👑 Celvass Admin] | [📊 1.Vendas] [🍲 2.Menu/Stok] [🪑 3.Meza/QR] [👥 4.Staff] [📈 5.Relatóriu] | [🖨️ QR Folha] [🚪 Sai] |
+---------------------------------------------------------------------------------------------------------+
```
* **Tab 1: Vendas & Métricas**: Dashboard finanseiru, rendimentu ohin, okupasaun meza, pedidu rekuza.
* **Tab 2: Katalógu Menu & Stok**: CRUD hahán, folin, disponivel / hotu ona.
* **Tab 3: Jestaun Meza & QR**: Kria meza foun, regenere token QR seguru, download QR individual.
* **Tab 4: Funsionáriu & Konta**: Kria konta kaixa, koki, no admin ho senha seguru.
* **Tab 5: Relatóriu & Fechu Kaixa**: Visualiza relatóriu Z-Report, istóriku transasaun.
* **Atalho Lais**: Butaun imprimi Folha QR kompletu (`/admin-portal/qr-sheet/`) no butaun Logout.

---

### 5.4. Cashier POS Flying Dock
Sentru kontrolu kaixa hetan **Flying Glass Dock** ne'ebé permite kasir kontrola sesaun no verifika fila tama ho klik ida de'it:
```
+----------------------------------------------------------------------------------------------------+
| [💰 Celvass Kaixa] | [🔔 1.POS & Fila (3)] [🕒 2.Istóriku] [🧾 3.Husu Konta (1)] [💼 4.Fechu Z] | [🔄 Refresh] [🚪 Sai] |
+----------------------------------------------------------------------------------------------------+
```
* Ativu badge ba pedidu foun tama (`pending-count-badge`).
* Ativu badge ba alerta meza ne'ebé husu konta (`bill-alerts-count`).

---

### 5.5. Kitchen KDS Flying Dock
Iha dapur, xefe koki presiza ekran ne'ebé mós husi distrasaun, maibé ho kontrolu esensiál ne'ebé fasil hanehan husi dook:
```
+----------------------------------------------------------------------------------------------------+
| [🔥 Celvass Dapur] | [📋 Fila Ativu] | [🔔 Audio: ON/OFF] | [⛶ Fullscreen] | [🔄 Refresh] | [🚪 Sai] |
+----------------------------------------------------------------------------------------------------+
```
* **Audio Toggle**: Kasir ka xefe koki bele ativa/dezativa lian sinu automátiku (bell sound) bainhira pedidu foun tama.
* **Fullscreen Button**: Hanehan hodi halo KDS tama ba módulu fullscreen TV monitor dapur.

---

## 6. ARSITEKTURA TIPOGRAFIA DINÁMIKA (FLUID RESPONSIVE TYPOGRAPHY HO CSS CLAMP)

Atu garante katak testu la bele lakon ka sai boot demais iha kualkér devaisu, sistema la bele uza medida `rem` estátiku ba titulu no etiketa. Sistema sei adota **Fluid Dynamic Typography Engine** liuhosi CSS `clamp(min, preferred, max)`:

```css
:root {
  /* Fluid Typographic Tokens */
  --font-hero-title: clamp(1.4rem, 4vw, 2.4rem);
  --font-section-title: clamp(1.1rem, 2.8vw, 1.6rem);
  --font-card-title: clamp(0.82rem, 2.4vw, 1.05rem);
  --font-card-desc: clamp(0.68rem, 1.8vw, 0.85rem);
  --font-card-price: clamp(1rem, 2.6vw, 1.35rem);
  --font-badge: clamp(0.62rem, 1.5vw, 0.75rem);
  --font-dock-label: clamp(0.65rem, 1.6vw, 0.75rem);
}
```

---

## 7. CETAK BIRU KODE IMPLEMENTASI KOMPLETU (FILE-BY-FILE TECHNICAL BLUEPRINT)

### 7.1. Modifikasaun `static/css/style.css`

Aumenta kódigu tuir mai iha `static/css/style.css` hodi implementa **macOS Flying Glass Dock** no **Compact Dynamic Food Cards**:

```css
/* ==========================================================================
   MACOS-STYLE FLYING GLASS DOCK & FLUID TYPOGRAPHY ENGINE (UI1.MD)
   ========================================================================== */

:root {
  --dock-bg: rgba(12, 17, 28, 0.88);
  --dock-border: rgba(255, 255, 255, 0.12);
  --dock-glow: rgba(249, 115, 22, 0.35);
  --dock-radius: 9999px; /* Pure Pill */
}

/* 1. Universal Flying Glass Dock Container */
.flying-glass-dock-wrapper {
  position: sticky;
  top: 0.85rem;
  z-index: 1040;
  display: flex;
  justify-content: center;
  padding: 0 1rem;
  pointer-events: none; /* Allows click through on margins */
}

.flying-glass-dock {
  pointer-events: auto;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: var(--dock-bg);
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  border: 1px solid var(--dock-border);
  border-radius: var(--dock-radius);
  padding: 0.4rem 0.8rem;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55), 0 0 1px 1px rgba(255, 255, 255, 0.08);
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  max-width: 95vw;
}

.flying-glass-dock:hover {
  border-color: rgba(249, 115, 22, 0.35);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.65), 0 0 25px rgba(249, 115, 22, 0.15);
}

/* Dock Item / Icon / Tab */
.dock-item-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  padding: 0.45rem 0.85rem;
  border-radius: var(--dock-radius);
  color: rgba(255, 255, 255, 0.72);
  text-decoration: none;
  font-family: 'Outfit', sans-serif;
  font-size: 0.82rem;
  font-weight: 600;
  border: 1px solid transparent;
  background: transparent;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}

.dock-item-btn i {
  font-size: 1.05rem;
  transition: transform 0.22s ease, color 0.22s ease;
}

/* macOS Magnification Zoom on Hover */
.dock-item-btn:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
  transform: translateY(-2px) scale(1.05);
}

.dock-item-btn:hover i {
  transform: scale(1.18);
  color: var(--primary-color);
}

/* Active State in Dock */
.dock-item-btn.active {
  background: var(--primary-gradient) !important;
  color: #ffffff !important;
  border-color: var(--primary-color) !important;
  box-shadow: 0 4px 18px var(--primary-glow) !important;
  transform: translateY(-1px);
}

.dock-divider {
  width: 1px;
  height: 24px;
  background: rgba(255, 255, 255, 0.12);
  margin: 0 0.2rem;
}

/* 2. Compact Interactive Food Cards (Mobile & Multi-Device) */
.menu-card {
  cursor: pointer; /* Whole card is interactive */
  position: relative;
}

.menu-card-floating-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 3;
  background: rgba(12, 17, 28, 0.75);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.2);
  color: #fed7aa;
  font-family: 'Outfit', sans-serif;
  font-size: clamp(0.62rem, 1.5vw, 0.72rem);
  font-weight: 700;
  border-radius: var(--radius-pill);
  padding: 0.2rem 0.55rem;
  transition: all 0.2s ease;
}

.menu-card-floating-badge:hover {
  background: var(--primary-color);
  color: #000000;
  border-color: var(--primary-color);
  transform: scale(1.08);
}

.menu-card-title {
  width: 100% !important; /* Full width so title never gets squeezed */
  font-size: var(--font-card-title, 0.95rem) !important;
  font-weight: 700 !important;
  line-height: 1.25 !important;
  margin-bottom: 0.25rem !important;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.menu-card-desc-compact {
  font-size: var(--font-card-desc, 0.75rem);
  color: rgba(255, 255, 255, 0.6);
  line-height: 1.35;
  margin-bottom: 0.5rem;
  display: -webkit-box;
  -webkit-line-clamp: 1; /* 1-Line micro summary on mobile */
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Mobile Specific Optimizations (< 768px) */
@media (max-width: 767.98px) {
  .flying-glass-dock-wrapper {
    top: 0.4rem;
    padding: 0 0.5rem;
  }
  
  .flying-glass-dock {
    padding: 0.3rem 0.5rem;
    gap: 0.2rem;
  }

  .dock-item-btn {
    padding: 0.35rem 0.55rem;
    font-size: 0.74rem;
  }

  /* 2-Column Grid food item padding */
  #menu-grid .menu-item-col {
    padding-left: 0.25rem !important;
    padding-right: 0.25rem !important;
    margin-bottom: 0.5rem !important;
  }

  .menu-card-body {
    padding: 0.6rem 0.65rem !important;
  }

  .menu-card-price {
    font-size: 1.05rem !important;
  }

  .btn-add-menu {
    padding: 0.3rem 0.65rem !important;
    font-size: 0.74rem !important;
  }
}
```

---

### 7.2. Modifikasaun `templates/base/base.html`

Iha `templates/base/base.html`, troka navbar tradisionál ne'ebé luan ho **macOS Flying Glass Dock** dinámiku tuir kargu:

```html
<!-- REFACTOR NAVBAR TO MACOS FLYING GLASS DOCK -->
<header class="flying-glass-dock-wrapper">
  <nav class="flying-glass-dock" aria-label="Navegasaun Prinsipál">
    
    <!-- Brand Logo -->
    <a href="{% if user.is_authenticated %}{% if user.is_admin %}/admin-portal/{% elif user.is_cashier %}/cashier/{% elif user.is_kitchen %}/kitchen/{% else %}/{% endif %}{% else %}/{% endif %}" class="dock-item-btn text-white fw-bold">
      <div class="brand-logo-icon" style="width: 28px; height: 28px; font-size: 0.85rem;">
        <i class="fa-solid fa-utensils"></i>
      </div>
      <span class="d-none d-sm-inline">{{ current_restaurant.name }}</span>
    </a>

    <div class="dock-divider"></div>

    <!-- ROLE SPECIFIC DOCK ITEMS -->
    {% if user.is_authenticated %}
      {% if user.is_admin %}
        <!-- ADMIN DEDICATED ITEMS (NO CASHIER/KITCHEN LINKS!) -->
        <a href="#menu-panel" class="dock-item-btn active" data-bs-toggle="pill" data-bs-target="#menu-panel">
          <i class="fa-solid fa-bowl-food text-warning"></i>
          <span class="d-none d-md-inline" data-i18n="menu_stock">Menu &amp; Stok</span>
        </a>
        <a href="#tables-panel" class="dock-item-btn" data-bs-toggle="pill" data-bs-target="#tables-panel">
          <i class="fa-solid fa-table-cells text-info"></i>
          <span class="d-none d-md-inline" data-i18n="tables_qr">Meza &amp; QR</span>
        </a>
        <a href="#users-panel" class="dock-item-btn" data-bs-toggle="pill" data-bs-target="#users-panel">
          <i class="fa-solid fa-users text-success"></i>
          <span class="d-none d-md-inline" data-i18n="staff_users">Staff</span>
        </a>
        <a href="#reports-panel" class="dock-item-btn" data-bs-toggle="pill" data-bs-target="#reports-panel">
          <i class="fa-solid fa-chart-pie text-warning"></i>
          <span class="d-none d-md-inline" data-i18n="reports">Relatóriu</span>
        </a>
      {% elif user.is_cashier %}
        <!-- CASHIER DEDICATED ITEMS -->
        <a href="#live-pos-panel" class="dock-item-btn active" data-bs-toggle="pill" data-bs-target="#live-pos-panel">
          <i class="fa-solid fa-cash-register text-warning"></i>
          <span class="d-none d-sm-inline">POS &amp; Fila</span>
        </a>
        <a href="#orders-history-panel" class="dock-item-btn" data-bs-toggle="pill" data-bs-target="#orders-history-panel" onclick="cashierApp.loadTodayOrdersHistory()">
          <i class="fa-solid fa-clock-rotate-left text-info"></i>
          <span class="d-none d-md-inline">Istóriku</span>
        </a>
        <a href="#shift-close-panel" class="dock-item-btn" data-bs-toggle="pill" data-bs-target="#shift-close-panel" onclick="cashierApp.loadShiftSummary()">
          <i class="fa-solid fa-vault text-success"></i>
          <span class="d-none d-md-inline">Fechu Kaixa</span>
        </a>
      {% elif user.is_kitchen %}
        <!-- KITCHEN DEDICATED ITEMS -->
        <button class="dock-item-btn active" onclick="kdsApp.loadQueue()">
          <i class="fa-solid fa-fire text-warning"></i>
          <span class="d-none d-sm-inline">Fila Dapur</span>
        </button>
        <button class="dock-item-btn" onclick="toggleKdsAudio()" id="btn-kds-audio">
          <i class="fa-solid fa-volume-high text-info" id="kds-audio-icon"></i>
          <span class="d-none d-md-inline">Audio</span>
        </button>
        <button class="dock-item-btn" onclick="toggleFullscreen()">
          <i class="fa-solid fa-expand text-success"></i>
          <span class="d-none d-md-inline">Fullscreen</span>
        </button>
      {% endif %}
    {% else %}
      <!-- CUSTOMER TOP DOCK ITEMS -->
      <a href="javascript:void(0)" onclick="const s = document.getElementById('menu-search'); if (s) { s.scrollIntoView({behavior:'smooth'}); s.focus(); }" class="dock-item-btn">
        <i class="fa-solid fa-magnifying-glass text-warning"></i>
        <span class="d-none d-sm-inline" data-i18n="search">Buka</span>
      </a>
      <a href="javascript:void(0)" data-bs-toggle="offcanvas" data-bs-target="#cartOffcanvas" class="dock-item-btn position-relative">
        <i class="fa-solid fa-basket-shopping text-warning"></i>
        <span class="badge bg-warning text-dark rounded-pill" id="top-dock-cart-badge" style="display:none; font-size:0.65rem;">0</span>
        <span class="d-none d-sm-inline" data-i18n="cart_title">Karreta</span>
      </a>
    {% endif %}

    <div class="dock-divider"></div>

    <!-- LANGUAGE SELECTOR PILL -->
    <div class="dropdown">
      <button class="dock-item-btn dropdown-toggle" type="button" data-bs-toggle="dropdown" id="current-lang-label">
        🇹🇱 <span class="d-none d-md-inline">Tetun</span>
      </button>
      <ul class="dropdown-menu dropdown-menu-end shadow">
        <li><a class="dropdown-item" href="javascript:void(0)" onclick="setLanguage('tet')">🇹🇱 Tetun</a></li>
        <li><a class="dropdown-item" href="javascript:void(0)" onclick="setLanguage('pt')">🇵🇹 Português</a></li>
        <li><a class="dropdown-item" href="javascript:void(0)" onclick="setLanguage('en')">🇬🇧 English</a></li>
        <li><a class="dropdown-item" href="javascript:void(0)" onclick="setLanguage('id')">🇮🇩 Indonesia</a></li>
      </ul>
    </div>

    <!-- USER LOGOUT OR LOGIN BUTTON -->
    {% if user.is_authenticated %}
      <a href="{% url 'logout' %}" class="dock-item-btn text-danger" title="Sai (Logout)">
        <i class="fa-solid fa-arrow-right-from-bracket"></i>
      </a>
    {% else %}
      <a href="{% url 'login' %}" class="dock-item-btn text-warning" title="Portal Staff">
        <i class="fa-solid fa-shield-halved"></i>
      </a>
    {% endif %}

  </nav>
</header>
```

---

### 7.3. Modifikasaun `templates/customer/index.html`

Iha `templates/customer/index.html`, atualiza kartaun hahán atu sai **compact, 100% visible title, interactive category chip, no whole card clickable**:

```html
<!-- KARTAUN HAHÁN FOUN HO FLOATING CATEGORY CHIP & INTERAKTIVU -->
<div class="col-md-4 col-sm-6 menu-item-col" data-category="{{ cat.slug }}" data-name="{{ item.name }}" data-desc="{{ item.description }}">
  <div class="menu-card" onclick="openItemModal('{{ item.id }}', '{{ item.name|escapejs }}', '{{ item.price }}', '{{ item.display_image|escapejs }}', '{{ item.description|escapejs }}')">
    
    <!-- Image Wrapper with Floating Interactive Category Chip -->
    <div class="menu-card-img-wrapper position-relative">
      <img src="{{ item.display_image }}" class="menu-card-img" alt="{{ item.name }}" loading="lazy" onerror="this.onerror=null; this.src='/static/icons/default-food.png';">
      
      <!-- Interactive Category Chip (Top-Right) -->
      <span class="menu-card-floating-badge" onclick="event.stopPropagation(); filterByCategory('{{ cat.slug }}');">
        <i class="fa-solid fa-tag me-1"></i>{{ cat.name }}
      </span>

      {% if item.is_featured %}
        <span class="badge bg-warning text-dark fw-bold position-absolute top-0 start-0 m-2 rounded-pill px-2 py-1 shadow" style="font-size: 0.65rem;">
          <i class="fa-solid fa-star me-1"></i> <span data-i18n="chefs_special">Chef</span>
        </span>
      {% endif %}

      <span class="badge bg-black bg-opacity-75 text-white position-absolute bottom-0 end-0 m-2 rounded-pill px-2 py-1 small" style="font-size: 0.65rem;">
        <i class="fa-regular fa-clock me-1 text-warning"></i> 10-15m
      </span>
    </div>

    <!-- Card Body: Full Width Title & Micro-Description -->
    <div class="menu-card-body">
      <h6 class="menu-card-title text-white">{{ item.name }}</h6>
      
      <p class="menu-card-desc-compact">
        {{ item.description|default:"Ingredientes fresku espesiál Celvass Resto & Bar." }}
      </p>

      <div class="d-flex justify-content-between align-items-center mt-auto pt-2 border-top border-white border-opacity-10">
        <div class="menu-card-price">${{ item.price }}</div>

        {% if item.availability == 'AVAILABLE' %}
          {% if session and session.is_active %}
            <button class="btn-add-menu btn-touch-target" onclick="event.stopPropagation(); openItemModal('{{ item.id }}', '{{ item.name|escapejs }}', '{{ item.price }}', '{{ item.display_image|escapejs }}', '{{ item.description|escapejs }}')">
              <i class="fa-solid fa-plus"></i> <span data-i18n="add">Aumenta</span>
            </button>
          {% else %}
            <button class="btn btn-sm btn-outline-secondary rounded-pill px-2 py-1" onclick="event.stopPropagation(); showToast(t('session_not_open_toast'), 'warning')" style="font-size: 0.72rem;">
              <i class="fa-solid fa-lock me-1"></i> <span data-i18n="locked">Xave</span>
            </button>
          {% endif %}
        {% else %}
          <span class="badge bg-danger bg-opacity-25 border border-danger text-danger rounded-pill px-2 py-1" style="font-size: 0.7rem;" data-i18n="sold_out">Hotu</span>
        {% endif %}
      </div>
    </div>

  </div>
</div>
```

---

### 7.4. Modifikasaun `templates/admin_custom/dashboard.html`

Hamoos navegasaun tab tradisional ne'ebé duplika no integra ho **Admin Flying Dock**. Hamoos link direta ba Kaixa no Dapur husi kualkér painél admin.

---

### 7.5. Modifikasaun `static/js/customer.js`

Aumenta funsaun `filterByCategory(slug)` atu fasilita klik direta husi Floating Category Chip:

```javascript
function filterByCategory(slug) {
  // Update active category pill
  document.querySelectorAll('.cat-pill').forEach((btn) => {
    if (btn.dataset.category === slug) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  // Filter menu grid items
  const items = document.querySelectorAll('.menu-item-col');
  items.forEach((col) => {
    if (slug === 'all' || col.dataset.category === slug) {
      col.style.display = 'block';
    } else {
      col.style.display = 'none';
    }
  });

  showToast(`Filtradu tuir kategoria: ${slug}`, 'info');
}
```

---

## 8. MATRIKS VERIFIKASAUN & PANDUAN QA TESTING MULTI-DEVICE

| Test Case ID | Devaisu / Resoluçaun | Pasu Verifikasaun (Test Action) | Rezultadu Esperadu (Expected Result) |
| :--- | :--- | :--- | :--- |
| **TC-UI-01** | Mobile (iPhone SE 375px) | Loke `/t/<token>/`, scroll haree menu hahán iha koluna 2 | Naran hahán mosu 100% klaru, la korta, la subar kotuk badge kategoria. |
| **TC-UI-02** | Mobile (Pixel 7 412px) | Klik iha foto hahán ka titulu | `itemCustomModal` loke kedas ho foto boot no kuantidade. |
| **TC-UI-03** | Mobile (Galaxy S23 360px) | Klik iha Floating Category Chip iha leten foto | Katalogu filtra kedas ba kategoria ne'e ho toast konfirmasaun. |
| **TC-UI-04** | Desktop (Laptop 1366px) | Loke `/admin-portal/` hanesan Admin | Header leten mosu hanesan **macOS Flying Dock** sentralizadu. La iha link ba Kaixa/Dapur. |
| **TC-UI-05** | Tablet (iPad 768px - 1024px)| Loke `/cashier/` hanesan Kasir | Dock POS melayang iha leten; painél fila tama no lista meza mosu 50%-50% lado-a-lado. |
| **TC-UI-06** | TV Screen (1920x1080) | Loke `/kitchen/` hanesan Koki | Dock Dapur iha butaun Fullscreen & Audio toggle; kanban 3 koluna fiksa 100vh. |

---

## 9. INSTRUBSAUN BA JUNIOR DEVELOPER & LOW-COST AI AGENTS

Bainhira ita boot (developer ka AI agent) atu ezekuta mudansa tuir failu ne'e, tuir pasu rigorozu tuir mai:
1. **Labele muda logika backend ka API**: Mudansa ne'e 100% mak frontend UI/UX, CSS, no template HTML/JS.
2. **Labele troka lian baze**: Mantein Tetun Prasa hanesan lian prinsipál iha sistema.
3. **Mantein Kór & Glassmorphism**: Uza sempri kór obsidian nakukun (`rgba(12, 17, 28, ...)`), blur 28px, no kór amber `#f97316`.
4. **Verifika ho Pytest**: Hafoin halo mudansa ba template, ezekuta `pytest` atu garante 14 teste automatizadu kontinua pass 100%.
5. **Commit & Deploy**: Commit ho mensajen padraun git:
   `feat(ui): implement compact mobile food cards and macOS flying glass dock (ui1.md)`
   hafoin deploy ba produsaun Vercel ho `vercel --prod --yes`.

---
*Dokumentu ne'e validu no prontu ba ezekusaun imediata.*
