# raspberry_home
Pressure and temperature

sudo apt update
sudo apt install -y python3-pip python3-venv
mkdir -p ~/pressure_web && cd ~/pressure_web
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn adafruit-circuitpython-ads1x15


mkdir -p static
nano static/index.html


source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000

sudo nano /etc/systemd/system/pressure-web.service

sudo systemctl daemon-reload
sudo systemctl enable --now pressure-web
sudo systemctl status pressure-web

