FROM codemowers/microservice-base
RUN pip3 install kopf
ADD /app /app
ENTRYPOINT /app/harbor-operator.py
