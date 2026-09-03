web: gunicorn myproject.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --capture-output --enable-stdio-inheritance
release: python manage.py migrate && python manage.py collectstatic --noinput
