######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################

# リモート LLM/VLM 前提で Video Search and Summarization を EC2 に展開する手順

このドキュメントは、Vision Language Model (VLM) と Large Language Model (LLM) をいずれもクラウド提供の NIM / OpenAI 互換 API に任せ、EC2 上では VSS エンジンと周辺ストレージ (Milvus, Neo4j, ArangoDB, MinIO) だけを動かす構成を対象としています。

## 前提条件
- AWS アカウントと EC2 で GPU インスタンスを作成できる権限
- NVIDIA API Key (build.nvidia.com) – LLM/Embedding/Reranker 用
- VLM 用の OpenAI 互換 API Key とエンドポイント (OpenAI / Azure OpenAI / NGC NIM など)
- このリポジトリにアクセスできる Git 環境

> **メモ:** g4dn.xlarge (T4 16GB) のような最小構成でも検証用途なら動作します。複数ストリームや CV パイプラインを有効化する場合は g5/g6 系インスタンスを推奨します。

## 1. EC2 インスタンスを用意
1. リージョンと VPC/サブネットを選定し、GPU 対応インスタンスを起動  
   - 推奨 OS: Ubuntu 22.04 ベースの NVIDIA DLAMI (CUDA/ドライバ済)  
   - セキュリティグループ: 22/tcp、Backend API (既定 8100/tcp)、Frontend UI (既定 9100/tcp) を許可  
   - IAM ロール: 必要に応じて S3 などのリソースを参照できるポリシーをアタッチ
2. 起動後、SSH でログインし以下を確認
   ```bash
   nvidia-smi
   ```
   ドライバが読み込まれていれば準備完了です。DLAMI 以外を使う場合は、CUDA ドライバと NVIDIA Container Toolkit を先に導入してください。

## 2. 基本ソフトウェアをインストール
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker
```
CloudWatch Agent や SSM Session Manager を使う場合は、同時に有効化しておくと運用が楽になります。

## 3. リポジトリを取得
```bash
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git
cd video-search-and-summarization/deploy/docker/remote_vlm_deployment
```

## 4. 環境変数ファイルを整備
1. サンプル `.env` をコピーして編集しやすいようにします。
   ```bash
   cp .env .env.ec2
   ```
2. `.env.ec2` 内の `***` や既定値を実環境に合わせて書き換えます。
   - `NVIDIA_API_KEY`: build.nvidia.com から取得したキー (LLM/Embedding/Reranker 用)
   - `OPENAI_API_KEY`, `VIA_VLM_ENDPOINT`, `VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME`: 使用する VLM エンドポイント情報
   - `GRAPH_DB_USERNAME/PASSWORD`, `MINIO_ROOT_USER/PASSWORD`, `ARANGO_DB_PASSWORD`: セキュリティ要件に応じて変更
   - `DISABLE_CV_PIPELINE`: CV パイプラインを使わない場合は `true` のまま
   - パス指定 (`CA_RAG_CONFIG` など) を相対パスのまま使う場合は、本ディレクトリから `docker compose` を実行してください。
3. シェルに読み込みます。
   ```bash
   set -a
   source .env.ec2
   set +a
   ```

## 5. コンテナ群を起動
1. Docker から GPU が見えているか確認
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
   ```
2. サービスをデタッチモードで起動
   ```bash
   docker compose --file compose.yaml up -d
   ```
3. VSS エンジンが ready になるまでログを監視
   ```bash
   docker compose logs -f via-server
   ```

## 6. 動作確認
- フロントエンド: `http://<EC2 のパブリック IP または DNS>:9100/`
- Backend API: `http://<同上>:8100/`

プライベートサブネットで運用する場合は、SSH ポートフォワーディングや ALB + ACM 証明書での公開を検討してください。

## 7. 運用のヒント
- **永続化**: `/opt/vss-data` などのパスに EBS をマウントし、`.env.ec2` の `ASSET_STORAGE_DIR` / `MILVUS_DATA_DIR` / `MINIO_*` を更新すると再起動に強い構成になります。
- **セキュリティ**: セキュリティグループをアクセス元 IP に限定し、必要なら Nginx や ALB で TLS / 認証を追加。
- **更新**: 新しいイメージを取得する際は `docker compose pull && docker compose up -d` を実行。
- **モニタリング**: CloudWatch / Grafana で GPU 使用率、コンテナ状態、API レイテンシを可視化することを推奨します。

## トラブルシューティング
- コンテナが GPU を認識しない場合は、`/etc/nvidia-container-runtime/config.toml` とドライババージョンを確認。
- LLM や VLM へのリクエストが失敗する場合は、`docker compose logs via-server` のメッセージと API Key/エンドポイント設定を再確認。

---

この手順で、VLM と LLM をリモートサービスに委譲しつつ、EC2 上で VSS を安全に運用できます。用途に応じてインスタンスサイズや外部ストレージ、監視体制を調整してください。
