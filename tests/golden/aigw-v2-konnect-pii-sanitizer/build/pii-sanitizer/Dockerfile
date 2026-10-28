FROM kong/ai-pii-service:v0.3.1-ja
RUN python -m spacy download en_core_web_lg
COPY nlp_engine_conf.yml /app/ai_pii_service/nlp_engine_conf.yml
