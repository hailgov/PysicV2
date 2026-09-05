cd .venv
cd Scripts

call activate.bat

cd ..
cd ..

pip install -r ./assets/req.txt

python ./backend/server.py