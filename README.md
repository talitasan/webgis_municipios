# WebGIS Municipal — Consulta de Imóveis

Sistema web para consulta de imóveis a partir do GeoPackage gerado no QGIS.

## Estrutura

```
webgis/
├── app.py              ← Servidor Flask (API)
├── requirements.txt    ← Dependências Python
├── dados/
│   └── municipio.gpkg  ← Seu arquivo QGIS
└── templates/
    └── index.html      ← Interface web do mapa
```

## Como rodar localmente

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Iniciar o servidor
```bash
python app.py
```

### 3. Acessar no navegador
```
http://localhost:5000
```

## Funcionalidades

- **Busca rápida**: por logradouro, bairro ou CPD
- **Filtros avançados**: logradouro, número, bairro, lote, CPD
- **Mapa interativo**: Leaflet + OpenStreetMap
- **Popup de detalhes**: todos os dados do imóvel ao clicar

## Campos disponíveis (camada `zona_viabilidade`)

| Campo interno         | Exibido como   |
|-----------------------|----------------|
| `_IMOVEIS_nrCPD`      | CPD            |
| `_IMOVEIS_nrCadDV`    | Cadastro       |
| `_IMOVEIS_nmLogradouro` | Logradouro   |
| `_IMOVEIS_nrImovel`   | Número         |
| `_IMOVEIS_nmBairro`   | Bairro         |
| `_IMOVEIS_nrLote`     | Lote           |
| `_IMOVEIS_AreaTerreno`| Área (m²)      |
| `_IMOVEIS_ZonaViabilidade` | Zona      |
| `_IMOVEIS_Matricula`  | Matrícula      |

## Deploy gratuito (Render.com)

1. Crie conta em https://render.com
2. Suba o projeto no GitHub
3. Crie um "Web Service" apontando para o repositório
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app`
6. Adicione `gunicorn` ao requirements.txt

## API endpoints

| Endpoint | Parâmetros | Descrição |
|---|---|---|
| `GET /api/buscar` | `q`, `logradouro`, `numero`, `bairro`, `lote`, `cpd` | Buscar imóveis |
| `GET /api/bairros` | — | Listar bairros |
| `GET /api/zonas` | — | Listar zonas de viabilidade |
