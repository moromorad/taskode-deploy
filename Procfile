web: gunicorn myproject.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2
release: python manage.py migrate && python manage.py collectstatic --noinput
