sudo cp apps/rag_service/deploy/systemd/rag-service-consumer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rag-service-consumer.service
sudo systemctl status rag-service-consumer.service

sudo systemctl daemon-reload
sudo systemctl restart rag-service-consumer.service
journalctl -u rag-service-consumer.service -f