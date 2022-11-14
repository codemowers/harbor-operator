FROM harbor.k-space.ee/k-space/microservice-base
RUN pip3 install kopf
ADD /app /app
ENTRYPOINT /app/harbor-operator.py
