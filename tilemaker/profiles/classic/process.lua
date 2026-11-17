-- Minimal OpenMapTiles-compatible profile for osm-tilegen (tilemaker 3.x).
-- Produces the layers/attributes used by styles/basic. No sea polygons yet
-- (they need a coastline shapefile, see CLAUDE.md, "Morza i linia brzegowa").

node_keys = {}

function init_function() end
function exit_function() end

function way_function()
  local highway = Find("highway")
  local railway = Find("railway")
  local natural = Find("natural")
  local landuse = Find("landuse")
  local leisure = Find("leisure")
  local waterway = Find("waterway")
  local water = Find("water")
  local boundary = Find("boundary")
  local admin = Find("admin_level")
  local building = Find("building")
  local isClosed = IsClosed()

  -- Roads
  if highway ~= "" then
    local class
    if highway == "motorway" or highway == "motorway_link" then class = "motorway"
    elseif highway == "trunk" or highway == "trunk_link" then class = "trunk"
    elseif highway == "primary" or highway == "primary_link" then class = "primary"
    elseif highway == "secondary" or highway == "secondary_link" then class = "secondary"
    elseif highway == "tertiary" or highway == "tertiary_link" then class = "tertiary"
    elseif highway == "residential" or highway == "unclassified" or highway == "living_street" then class = "minor"
    elseif highway == "service" then class = "service"
    elseif highway == "track" then class = "track"
    elseif highway == "path" or highway == "footway" or highway == "cycleway"
        or highway == "bridleway" or highway == "steps" or highway == "pedestrian" then class = "path"
    end
    if class then
      Layer("transportation", false)
      Attribute("class", class)
      Attribute("subclass", highway)
      local minz = 12
      if class == "motorway" or class == "trunk" then minz = 4
      elseif class == "primary" then minz = 6
      elseif class == "secondary" then minz = 8
      elseif class == "tertiary" then minz = 10 end
      MinZoom(minz)
    end
  elseif railway == "rail" then
    Layer("transportation", false)
    Attribute("class", "rail")
    MinZoom(8)
  end

  -- Waterways
  if waterway == "river" or waterway == "canal" or waterway == "stream" then
    Layer("waterway", false)
    Attribute("class", waterway)
    if waterway == "stream" then MinZoom(12) end
  end

  -- Water bodies
  if isClosed and (natural == "water" or landuse == "reservoir" or landuse == "basin" or water ~= "") then
    Layer("water", true)
    Attribute("class", "lake")
  end

  -- Landcover
  if isClosed then
    local lc
    if natural == "wood" or landuse == "forest" then lc = "wood"
    elseif landuse == "grass" or landuse == "meadow" or natural == "grassland" then lc = "grass"
    elseif landuse == "farmland" or landuse == "orchard" or landuse == "vineyard" then lc = "farmland"
    end
    if lc then
      Layer("landcover", true)
      Attribute("class", lc)
    end

    local lu
    if landuse == "residential" then lu = "residential"
    elseif landuse == "commercial" or landuse == "retail" then lu = "commercial"
    elseif landuse == "industrial" then lu = "industrial"
    elseif landuse == "cemetery" then lu = "cemetery"
    end
    if lu then
      Layer("landuse", true)
      Attribute("class", lu)
    end

    if leisure == "park" or leisure == "garden" or leisure == "nature_reserve"
        or boundary == "national_park" or boundary == "protected_area" then
      Layer("park", true)
      Attribute("class", leisure ~= "" and leisure or boundary)
    end

    if building ~= "" then
      Layer("building", true)
      MinZoom(13)
    end
  end

  -- Administrative boundaries drawn from boundary ways
  if boundary == "administrative" and admin ~= "" then
    local level = tonumber(admin)
    if level and level <= 8 then
      Layer("boundary", false)
      AttributeNumeric("admin_level", level)
      if level > 4 then MinZoom(8) end
    end
  end
end

function relation_scan_function()
  if Find("type") == "multipolygon" then
    Accept()
  end
end

function relation_function() end
