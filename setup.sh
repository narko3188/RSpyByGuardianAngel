#!/bin/bash
# SerbiaTracker - Script d'installation et lancement
set -e

echo "============================================"
echo "  SerbiaTracker - Installation"
echo "  Geolocalisation Serbie par telephone"
echo "============================================"

# 1. Installer les dependances Python
echo ""
echo "[1/5] Installation dependances Python..."
cd "$(dirname "$0")/backend"
pip install -r requirements.txt

# 2. Copier .env si non existant
echo ""
echo "[2/5] Configuration .env..."
if [ ! -f .env ]; then
    cp ../.env.template .env
    echo "  Fichier .env cree depuis le template"
    echo "  ⚠️  Editez .env et ajoutez vos cles API"
else
    echo "  .env existe deja"
fi

# 3. Telecharger la base antennes Serbie
echo ""
echo "[3/5] Base antennes Serbie..."
if [ ! -f data/cell_towers/serbia_towers.csv.gz ]; then
    echo "  Lancement telechargement depuis OpenCellID..."
    python ../scripts/download_serbia_towers.py
else
    echo "  Base antennes deja presente"
    ls -lh data/cell_towers/serbia_towers.csv.gz
fi

# 4. Initialiser la base de donnees
echo ""
echo "[4/5] Initialisation base de donnees..."
python -c "
import asyncio
from services.tower_database import tower_db
async def init():
    await tower_db.initialize()
    stats = await tower_db.get_stats()
    if stats['total_towers_serbia'] == 0:
        print('  Chargement antennes...')
        await tower_db.load_from_opencellid()
        stats = await tower_db.get_stats()
    print(f'  {stats[\"total_towers_serbia\"]} antennes en base')
asyncio.run(init())
"

# 5. Lancer le serveur
echo ""
echo "[5/5] Demarrage serveur..."
echo "  API: http://0.0.0.0:8000"
echo "  Docs: http://0.0.0.0:8000/docs"
echo ""
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
