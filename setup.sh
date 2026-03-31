#!bin/bash
cd backup
python -m venv .venv
source bin/activate
pip install -r requirements.txt
cd ..
cd frontend/frontend-shaarp
npm i
npm run dev