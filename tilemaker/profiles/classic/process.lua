-- "Classic" profile for osm-tilegen (tilemaker 3.x), OpenMapTiles-compatible subset.
-- Layers: transportation, transportation_name, place, water, waterway,
-- landcover, landuse, park, building, boundary.
-- No sea polygons yet (they need a coastline shapefile, see CLAUDE.md).

-- @NODE_KEYS@
node_keys = { "place", "natural", "tourism", "amenity", "historic", "man_made", "tower:type",
              "mountain_pass", "waterway", "information", "shelter", "access", "shop",
              "railway", "aeroway", "highway", "addr:housenumber" }

-- General-purpose POIs (OSM Carto-like). Returns class, minzoom, needs_name.
-- Data tiles stop at z14, so everything has minzoom <= 14 (labels overzoom).
function general_poi(name)
  local amenity = Find("amenity")
  local shop = Find("shop")
  local tourism = Find("tourism")
  local railway = Find("railway")
  if railway == "station" or railway == "halt" then
    if Find("subway") == "yes" or Find("light_rail") == "yes" then return "station", 13, true end
    return "station", 11, true
  end
  if railway == "tram_stop" then return "tram_stop", 14, true end
  if Find("highway") == "bus_stop" then return "bus_stop", 14, false end
  if Find("aeroway") == "aerodrome" then return "airport", 9, true end
  if amenity == "hospital" then return "hospital", 13, true end
  if amenity == "clinic" or amenity == "doctors" or amenity == "dentist" then return "clinic", 14, true end
  if amenity == "pharmacy" then return "pharmacy", 14, false end
  if amenity == "school" or amenity == "kindergarten" or amenity == "college" or amenity == "university" then
    return "school", 14, true
  end
  if amenity == "police" then return "police", 14, false end
  if amenity == "fire_station" then return "fire_station", 14, false end
  if amenity == "post_office" then return "post", 14, false end
  if amenity == "bank" then return "bank", 14, true end
  if amenity == "fuel" then return "fuel", 14, false end
  if amenity == "restaurant" or amenity == "fast_food" or amenity == "cafe" or amenity == "pub"
      or amenity == "bar" or amenity == "biergarten" then
    return "food", 14, true
  end
  if amenity == "place_of_worship" then return "worship", 14, true end
  if amenity == "townhall" or amenity == "library" or amenity == "theatre" or amenity == "cinema" then
    return "civic", 14, true
  end
  if shop == "supermarket" or shop == "mall" or shop == "department_store" then return "shop", 13, true end
  if shop ~= "" and shop ~= "vacant" then return "shop", 14, true end
  if tourism == "hotel" or tourism == "guest_house" or tourism == "hostel" or tourism == "motel" then
    return "lodging", 14, true
  end
  if tourism == "museum" or tourism == "gallery" or tourism == "zoo" or tourism == "theme_park"
      or tourism == "attraction" then
    return "attraction", 14, true
  end
  return nil
end

function init_function() end
function exit_function() end

function node_function()
  local place = Find("place")
  local name = Find("name")
  if place ~= "" and name ~= "" then
    local minz
    if place == "country" or place == "state" then minz = 2
    elseif place == "city" then minz = 4
    elseif place == "town" then minz = 7
    elseif place == "village" then minz = 10
    elseif place == "suburb" or place == "hamlet" or place == "neighbourhood" then minz = 12
    elseif place == "island" then minz = 8
    elseif place == "islet" or place == "isolated_dwelling" or place == "farm" or place == "locality" then minz = 13
    end
    if minz then
      Layer("place", false)
      Attribute("class", place)
      Attribute("name", name)
      MinZoom(minz)
    end
  end
  local natural = Find("natural")
  if natural == "peak" and name ~= "" then
    Layer("mountain_peak", false)
    Attribute("name", name)
    local ele = tonumber(Find("ele"))
    if ele then AttributeNumeric("ele", ele) end
    MinZoom(10)
    return
  end
  local class, minz, needs_name = general_poi(name)
  if class and (not needs_name or name ~= "") then
    Layer("poi", false)
    Attribute("class", class)
    Attribute("name", name)
    MinZoom(minz)
  end
  local hn = Find("addr:housenumber")
  if hn ~= "" then
    Layer("housenumber", false)
    Attribute("housenumber", hn)
    MinZoom(14)
  end
end

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
  local amenity = Find("amenity")
  local aeroway = Find("aeroway")
  local route = Find("route")
  local wname = Find("name")

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
      local minz = 12
      if class == "motorway" or class == "trunk" then minz = 4
      elseif class == "primary" then minz = 6
      elseif class == "secondary" then minz = 8
      elseif class == "tertiary" then minz = 10 end

      Layer("transportation", false)
      Attribute("class", class)
      Attribute("subclass", highway)
      local bridge, tunnel = Find("bridge"), Find("tunnel")
      if bridge ~= "" and bridge ~= "no" then Attribute("brunnel", "bridge")
      elseif tunnel ~= "" and tunnel ~= "no" then Attribute("brunnel", "tunnel") end
      -- @PATH_ATTRS@
      MinZoom(minz)

      local name = Find("name")
      if name ~= "" and class ~= "path" and class ~= "track" then
        Layer("transportation_name", false)
        Attribute("class", class)
        Attribute("name", name)
        Attribute("ref", Find("ref"))
        MinZoom(math.max(minz, 12))
      end
    end
  elseif railway == "rail" or railway == "light_rail" or railway == "tram"
      or railway == "subway" or railway == "narrow_gauge" then
    Layer("transportation", false)
    Attribute("class", "rail")
    Attribute("subclass", railway)
    if railway == "rail" then
      if Find("service") ~= "" then MinZoom(13) else MinZoom(8) end
    elseif railway == "narrow_gauge" then MinZoom(11)
    else MinZoom(12) end
    local rtunnel = Find("tunnel")
    if (rtunnel ~= "" and rtunnel ~= "no") or railway == "subway" then Attribute("brunnel", "tunnel") end
  elseif route == "ferry" then
    Layer("transportation", false)
    Attribute("class", "ferry")
    MinZoom(9)
  elseif aeroway == "runway" or aeroway == "taxiway" then
    Layer("transportation", false)
    Attribute("class", "aeroway")
    Attribute("subclass", aeroway)
    MinZoom(aeroway == "runway" and 10 or 12)
  end

  -- Waterways
  if waterway == "river" or waterway == "canal" or waterway == "stream" then
    Layer("waterway", false)
    Attribute("class", waterway)
    Attribute("name", wname)
    if waterway == "stream" then MinZoom(12) end
  end

  -- Water bodies
  if isClosed and (natural == "water" or landuse == "reservoir" or landuse == "basin" or water ~= "") then
    Layer("water", true)
    Attribute("class", "lake")
    local wtype = Find("water")
    if wname ~= "" and natural == "water" and wtype ~= "basin" and wtype ~= "wastewater" then
      LayerAsCentroid("water_name")
      Attribute("name", wname)
      MinZoom(12)
    end
  end

  -- Landcover
  if isClosed then
    local lc
    if natural == "wood" or landuse == "forest" then lc = "wood"
    elseif landuse == "grass" or landuse == "meadow" or natural == "grassland" then lc = "grass"
    elseif landuse == "farmland" or landuse == "orchard" or landuse == "vineyard" then lc = "farmland"
    elseif natural == "bare_rock" then lc = "rock"
    elseif natural == "scree" or natural == "shingle" then lc = "scree"
    elseif natural == "heath" or natural == "scrub" then lc = "scrub"
    elseif natural == "wetland" then lc = "wetland"
    elseif natural == "glacier" then lc = "glacier"
    elseif natural == "sand" or natural == "beach" then lc = "sand"
    end
    if lc then
      Layer("landcover", true)
      Attribute("class", lc)
    end

    local lu
    if landuse == "residential" then lu = "residential"
    elseif landuse == "commercial" or landuse == "retail" then lu = "commercial"
    elseif landuse == "industrial" then lu = "industrial"
    elseif landuse == "cemetery" or amenity == "grave_yard" then lu = "cemetery"
    elseif landuse == "military" then lu = "military"
    elseif landuse == "quarry" then lu = "quarry"
    elseif landuse == "allotments" then lu = "allotments"
    elseif landuse == "recreation_ground" or leisure == "recreation_ground" or leisure == "playground"
        or landuse == "village_green" then lu = "recreation"
    elseif landuse == "construction" or landuse == "brownfield" then lu = "construction"
    elseif landuse == "railway" then lu = "railway"
    elseif amenity == "school" or amenity == "kindergarten" or amenity == "college" or amenity == "university" then
      lu = "education"
    elseif amenity == "hospital" then lu = "hospital"
    elseif amenity == "parking" then lu = "parking"
    elseif leisure == "pitch" or leisure == "sports_centre" or leisure == "stadium" or leisure == "track"
        or leisure == "golf_course" then lu = "sports"
    elseif aeroway == "aerodrome" then lu = "airport"
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
      local hn = Find("addr:housenumber")
      if hn ~= "" then
        LayerAsCentroid("housenumber")
        Attribute("housenumber", hn)
        MinZoom(14)
      end
    end

    -- POIs mapped as areas (hospitals, schools, stations, shops ...)
    local pclass, pminz, pneeds = general_poi(wname)
    if pclass and pclass ~= "bus_stop" and pclass ~= "tram_stop" and (not pneeds or wname ~= "") then
      LayerAsCentroid("poi")
      Attribute("class", pclass)
      Attribute("name", wname)
      MinZoom(pminz)
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

  -- @WAY_EXTRA@
end

-- @RELATION_SCAN@
function relation_scan_function() end
