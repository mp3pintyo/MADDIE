# Upstream felmérés – Multi-Agents-Debate

Forrás:
- repo: https://github.com/estherliu02/Multi-Agents-Debate
- commit: `41b10b6c93526b25b97fb5ac31f97c46a8da6c5c`

Megállapítások:
- A projekt eredetileg research / experiment fókuszú.
- Tartalmaz interaktív futtatást és agent utilokat, de nem kész, modern webes chat UI-t.
- OpenAI és HuggingFace irányba is vannak utilok, ezért a vita-logika inspirációként jól használható.
- A jelen feladathoz szükséges GUI, advisor-selector, pontozás, export és per-message audio nem állt készen upstreamben.

Miért készült új app réteg:
- a feladat nem pusztán CLI debate script, hanem vizuális tanácsadói felület
- a felhasználó konkrét llama.cpp endpointot adott meg
- OmniVoice-alapú, karakterenkénti hangréteg is kellett

Megőrzött kapcsolat az upstreamhez:
- a többnézőpontos roundtable/debate gondolat és a szereplőnkénti megszólalások felépítése upstream-inspirált
- a tényleges webapp és runtime integráció új implementáció
