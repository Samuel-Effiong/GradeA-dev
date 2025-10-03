web: gunicorn AutoGrader.wsgi:application --bind 0.0.0.0:8000 --timeout 300
worker: python manage.py qcluster
