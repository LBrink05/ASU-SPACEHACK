# NeoNomad - ASU SPACEHACK APPLICATION

**NeoNomad** is a multimodal route planning software that combines graph-based routing with real-time satellite environmental data. Designed to give strategic, environmental and compliant transport routes, NeoNomad helps plan sustainable logistics with reduced risks. Our focus lies on minimizing *carbon emissions*, *optimize costs*, and ensure *EU ETS (Emissions Trading System)* compliance.

---

### Core Features
- **Route Planning**: Combined road, rail, and sea transport for optimal routes
- **ETS Compliance**: Real-time EU Emissions Trading System impact assessment
- **Real-Time Satellite Data**: Live pollutant monitoring (NO₂, CO, SO₂, O₃) via Google Earth Engine
- **Waypoint Generation**: Intelligent sampling along route corridors
- **Customizable Weighting**: Customize the importance of emissions, cost, compliance or transport time 

### Pollutants accounted for
- **NO₂ Column Density** (TROPOMI/S5P) - Industrial emissions indicator
- **CO Column Density** (TROPOMI/S5P) - Combustion efficiency metric
- **SO₂ Column Density** (TROPOMI/S5P) - Industrial activity proxy
- **O₃ Column Density** (TROPOMI/S5P) - Secondary pollutant indicator
- **AOD Proxy** (MODIS) - Particulate matter estimation

---

## Prerequisites

### System Requirements
- Python 3.8 or higher
- SQLite3
- Google Earth Engine account with project access
- 4GB RAM minimum (8GB recommended for satellite data processing)
- stable internet connection

### API & Authentication
- **Google Earth Engine**: Requires authentication via `earthengine authenticate`
- **Project ID**: Must have access to project id 

## Running Application

```bash
source env/bin/activate
pip install -r requirements
python app.py
```
