/* Photo Location - Moving marker visualisation with proportional leg durations and stations */

// ============================================================================
// Configuration
// ============================================================================

let imageRoute = []; // Will be populated from JSON data

// App configuration (loaded from /config.json)
let appConfig = {};
let offlineMode = true; // set after loading config
let exportPath = 'web_export'; // local folder for offline assets (relative to site root)

// JSON path for image data when using Supabase Storage
// Note: path is relative to bucket, bucket name is specified separately
let IMAGE_DATA_STORAGE_PATH = 'image_data.json';

// Supabase Storage bucket names (defaults)
let IMAGES_BUCKET = 'images';
let THUMBNAILS_BUCKET = 'thumbnails';
let DATA_BUCKET = 'data';

const stationDurationMs = 1500; // dwell per image location (except first/last)
const popUpDurationMs = 1400; // duration to show popup when arriving at a location
const baseLegDurationMs = 1000; // base per-leg duration used for proportional scaling
const arrivalThresholdMeters = 500; // distance threshold to consider "arrived" for popup

// Supabase Configuration
let SUPABASE_URL = '';
let SUPABASE_ANON_KEY = '';
let SHARED_EMAIL = '';


// ============================================================================
// Supabase Client Initialization
// ============================================================================

let supabaseClient = null;

function initializeSupabase() {
  // Wait for Supabase to be available (loaded from CDN)
  // The script tag in HTML makes createClient available via window.supabaseCreateClient
  // If running in offline mode we don't initialize Supabase
  if (offlineMode) {
    console.log('Offline mode enabled; skipping Supabase initialization');
    return true;
  }

  if (typeof window.supabaseCreateClient !== 'undefined') {
    try {
      // Validate that we have the required credentials
      if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
        showError('Supabase credentials not configured. Please check the setup instructions.');
        return false;
      }
      supabaseClient = window.supabaseCreateClient(SUPABASE_URL, SUPABASE_ANON_KEY);
      return true;
    } catch (error) {
      console.error('Error initializing Supabase:', error);
      showError('Failed to initialize authentication. Please check your Supabase configuration.');
      return false;
    }
  } else {
    console.error('Supabase createClient not available. Make sure Supabase script is loaded.');
    showError('Authentication service not loaded. Please refresh the page.');
    return false;
  }
}

// ============================================================================
// Authentication State
// ============================================================================

let isAuthenticated = false;

// ============================================================================
// Authentication Functions
// ============================================================================

async function checkSession() {
  // If offline, treat as authenticated
  if (offlineMode) {
    isAuthenticated = true;
    showContent();
    return true;
  }

  if (!supabaseClient) {
    return false;
  }

  try {
    const { data: { session }, error } = await supabaseClient.auth.getSession();
    if (error) {
      console.error('Error checking session:', error);
      return false;
    }

    if (session) {
      isAuthenticated = true;
      showContent();
      return true;
    } else {
      isAuthenticated = false;
      showLogin();
      return false;
    }
  } catch (error) {
    console.error('Error checking session:', error);
    showLogin();
    return false;
  }
}

async function handleLogin(email, password) {
  // If offline mode, bypass login UI (there is no auth)
  if (offlineMode) {
    isAuthenticated = true;
    showContent();
    if (!map) await initializeApp();
    return true;
  }

  if (!supabaseClient) {
    showError('Authentication service not available. Please refresh the page.');
    return false;
  }

  try {
    const { data, error } = await supabaseClient.auth.signInWithPassword({
      email: email,
      password: password
    });

    if (error) {
      showError(error.message || 'Login failed. Please check your credentials.');
      return false;
    }

    if (data.session) {
      isAuthenticated = true;
      showContent();
      // Initialize the app if not already initialized
      if (!map) {
        await initializeApp();
      }
      return true;
    } else {
      showError('Login failed. No session created.');
      return false;
    }
  } catch (error) {
    console.error('Login error:', error);
    showError('An unexpected error occurred. Please try again.');
    return false;
  }
}

async function handleLogout() {
  // If offline, nothing to do
  if (offlineMode) {
    return;
  }

  if (!supabaseClient) {
    return;
  }

  try {
    const { error } = await supabaseClient.auth.signOut();
    if (error) {
      console.error('Logout error:', error);
    }

    isAuthenticated = false;
    showLogin();
  } catch (error) {
    console.error('Logout error:', error);
    // Still show login even if logout fails
    isAuthenticated = false;
    showLogin();
  }
}

function showLogin() {
  const loginContainer = document.getElementById('login');
  const mapContent = document.getElementById('map-content');
  
  if (loginContainer) loginContainer.style.display = 'flex';
  if (mapContent) mapContent.style.display = 'none';
}

function showContent() {
  const loginContainer = document.getElementById('login');
  const mapContent = document.getElementById('map-content');
  
  if (loginContainer) loginContainer.style.display = 'none';
  if (mapContent) mapContent.style.display = 'block';
}

function showError(message) {
  const errorDiv = document.getElementById('loginError');
  if (errorDiv) {
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';
  } else {
    // Fallback if DOM not ready yet
    console.error('Error:', message);
  }
}

function hideError() {
  const errorDiv = document.getElementById('loginError');
  if (errorDiv) {
    errorDiv.style.display = 'none';
  }
}

// ============================================================================
// Global State
// ============================================================================

let map;
let routeLine = null;
let movingMarker = null;
let cityMarkers = []; // Markers for route animation (circle markers)
let clusterMarkers = []; // Markers for clustering (clickable markers)
let markerClusterGroup = null; // Marker cluster group
let currentPopupMarker = null;
let popupTimeoutId = null;
let lastArrivedIndex = null;
let popupIsShowing = false; // Track if a popup is currently being displayed
let clusterIconCache = new Map(); // Cache for cluster icons to avoid re-rendering

// UI elements - new video-player-style controls
const playPauseBtn = document.getElementById('playPauseBtn');
const playIcon = playPauseBtn?.querySelector('.play-icon');
const pauseIcon = playPauseBtn?.querySelector('.pause-icon');

// Playback state
let isPlaying = false;
let isPaused = false;

// ============================================================================
// Map Initialization
// ============================================================================

function initializeMap() {
  map = L.map('map', {
    zoomControl: true,
    scrollWheelZoom: false,   // disable default to use smooth wheel zoom
    smoothWheelZoom: true,
    smoothSensitivity: 5.0
  });
 
  // Basemap
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(map);
}

// ============================================================================
// Data Loading
// ============================================================================

/**
 * Get a signed URL for a file in Supabase Storage.
 * Signed URLs are required when storage buckets have authentication policies.
 */
async function getSignedUrl(bucket, path, expiresIn = 3600) {
  if (!supabaseClient) {
    throw new Error('Supabase client not initialized');
  }
  
  try {
    const { data, error } = await supabaseClient.storage
      .from(bucket)
      .createSignedUrl(path, expiresIn);
    
    if (error) {
      console.error(`Error creating signed URL for ${bucket}/${path}:`, error);
      throw error;
    }
    
    return data.signedUrl;
  } catch (error) {
    console.error(`Failed to get signed URL for ${bucket}/${path}:`, error);
    throw error;
  }
}

/**
 * Load image data from Supabase Storage.
 * The JSON file contains paths that reference Supabase Storage buckets.
 */
async function loadImageData() {
  // Clear loading flags when loading new data
  imageUrlLoadingFlags.clear();
  
  // Offline mode: load local JSON from exportPath
  if (offlineMode) {
    try {
      const localJsonPath = '/' + String(exportPath).replace(/\/$/, '') + '/image_data.json';
      const resp = await fetch(localJsonPath);
      if (!resp.ok) {
        throw new Error(`Failed to load local image data: ${resp.statusText}`);
      }
      const data = await resp.json();
      const sortedImages = (data.images || data).sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

      imageRoute = sortedImages.map(img => {
        // Support different field names; prefer explicit path fields if present
        const imgPath = img.path || img.storagePath || img.filename || '';
        const thumbPath = img.thumbnail || img.thumbnailStoragePath || imgPath;

        // Normalize to local web_export path if not absolute
        const mapLocal = (p) => {
          if (!p) return '';
          // If already an absolute URL (http(s) or protocol-relative), return as-is
          if (/^(https?:)?\/\//i.test(p)) return p;

          const exp = String(exportPath).replace(/^\/+|\/+$/g, ''); // e.g. "web_export"
          const pStr = String(p);

          // If the path already contains the exportPath segment, return the substring from there
          const idx = pStr.indexOf(exp);
          if (idx !== -1) {
            const sub = pStr.slice(idx).replace(/^\/+/, '');
            return '/' + sub; // e.g. "/web_export/IMG_..."
          }

          // Otherwise, prefix with normalized base
          const base = '/' + exp;
          return base + '/' + pStr.replace(/^\/+/, '');
        };

        const mappedImgPath = mapLocal(imgPath);
        const mappedThumbPath = mapLocal(thumbPath);

        return {
          name: img.filename,
          coords: [img.lat, img.lng],
          timestamp: img.timestamp,
          date: img.date,
          score: img.score,
          // Store mapped paths in storagePath fields - this allows offline mode
          // to use the same code path as online mode
          storagePath: mappedImgPath,
          thumbnailStoragePath: mappedThumbPath,
          // Leave these null initially - will be populated by ensureImageUrls
          path: mappedImgPath,
          thumbnail: mappedThumbPath
        };
      });

      return imageRoute;
    } catch (err) {
      console.error('Error loading local image data:', err);
      return [];
    }
  }

  // Online mode: require authentication and Supabase client
  if (!supabaseClient) {
    console.error('Supabase client not initialized');
    return [];
  }

  if (!isAuthenticated) {
    console.error('User not authenticated');
    return [];
  }

  try {
    // Get signed URL for image_data.json
    const jsonUrl = await getSignedUrl(DATA_BUCKET, IMAGE_DATA_STORAGE_PATH);

    // Fetch the JSON data
    const response = await fetch(jsonUrl);
    if (!response.ok) {
      throw new Error(`Failed to fetch image data: ${response.statusText}`);
    }

    const data = await response.json();

    // Sort images chronologically by timestamp
    const sortedImages = data.images.sort((a, b) => {
      return new Date(a.timestamp) - new Date(b.timestamp);
    });

    // Create route from image locations
    imageRoute = sortedImages.map(img => {
      // Store the storage paths (bucket/path format)
      const imageStoragePath = img.path; // e.g., "images/IMG_4943.webp"
      const thumbnailStoragePath = img.thumbnail || img.path; // e.g., "thumbnails/IMG_4943.webp"

      return {
        name: img.filename,
        coords: [img.lat, img.lng],
        timestamp: img.timestamp,
        date: img.date,
        score: img.score,
        // Store storage paths - we'll generate signed URLs on demand
        storagePath: imageStoragePath,
        thumbnailStoragePath: thumbnailStoragePath,
        // These will be populated with signed URLs when needed
        path: null,
        thumbnail: null
      };
    });

    return imageRoute;
  } catch (error) {
    console.error('Error loading image data:', error);
    return [];
  }
}

/**
 * Get signed URL for an image, caching it for a period.
 */
const imageUrlCache = new Map();
// Track in-progress URL loading to prevent race conditions
const imageUrlLoadingFlags = new Map();
const CACHE_DURATION_MS = 50 * 60 * 1000; // 50 minutes (signed URLs expire in 1 hour)

// Cache for image dimensions to maintain popup size
const imageDimensionsCache = new Map();

async function getImageUrl(storagePath) {  
  // Check cache first
  const cached = imageUrlCache.get(storagePath);
  if (cached && Date.now() - cached.timestamp < CACHE_DURATION_MS) {
    return cached.url;
  }
  
  // If offline, map to local export path
  if (offlineMode) {
    if (!storagePath) {
      console.log('[getImageUrl] No storage path provided');
      return '';
    }
    // If already an absolute URL (http(s) or protocol-relative), return as-is
    if (/^(https?:)?\/\//i.test(storagePath)) {
      imageUrlCache.set(storagePath, { url: storagePath, timestamp: Date.now() });
      return storagePath;
    }
    // Normalize exportPath and strip any parent directories before it
    const exp = String(exportPath).replace(/^\/+|\/+$/g, ''); // e.g. 'web_export'
    const sp = String(storagePath);
    const idx = sp.indexOf(exp);
    let mapped;
    if (idx !== -1) {
      mapped = '/' + sp.slice(idx).replace(/^\/+/, '');
    } else {
      mapped = '/' + exp + '/' + sp.replace(/^\/+/, '');
    }
    imageUrlCache.set(storagePath, { url: mapped, timestamp: Date.now() });
    return mapped;
  }
  
  // Extract bucket and path from storage path (format: "bucket/path")
  const parts = String(storagePath).replace(/^\/+/, '').split('/');
  const bucket = parts.shift();
  const path = parts.join('/');

  // Generate new signed URL
  const signedUrl = await getSignedUrl(bucket, path);

  // Cache it
  imageUrlCache.set(storagePath, {
    url: signedUrl,
    timestamp: Date.now()
  });

  return signedUrl;
}


async function ensureImageUrls(img) {
  // Create a unique key for this image
  const imgKey = img.name || `${img.coords[0]}_${img.coords[1]}`;
  
  // Check if we're already loading URLs for this image
  if (imageUrlLoadingFlags.has(imgKey)) {
    // Wait for the existing load to complete
    return await imageUrlLoadingFlags.get(imgKey);
  }
  
  // Create a promise for this load operation
  const loadPromise = (async () => {
    try {
      // Load path if needed
      if (!img.path && img.storagePath) {
        img.path = await getImageUrl(img.storagePath);
      }
      
      // Load thumbnail if needed
      if (!img.thumbnail && img.thumbnailStoragePath) {
        img.thumbnail = await getImageUrl(img.thumbnailStoragePath);
      }
      
      return img;
    } finally {
      // Clean up the loading flag when done
      imageUrlLoadingFlags.delete(imgKey);
    }
  })();
  
  // Store the promise so concurrent calls can wait for it
  imageUrlLoadingFlags.set(imgKey, loadPromise);
  
  return await loadPromise;
}


/**
 * Preload an image and get its dimensions.
 * This helps maintain popup size stability.
 */
function preloadImageAndGetDimensions(imageUrl) {
  return new Promise((resolve, reject) => {
    // Check cache first
    if (imageDimensionsCache.has(imageUrl)) {
      resolve(imageDimensionsCache.get(imageUrl));
      return;
    }
    
    const img = new Image();
    img.onload = () => {
      const dimensions = {
        width: img.naturalWidth,
        height: img.naturalHeight,
        aspectRatio: img.naturalWidth / img.naturalHeight
      };
      imageDimensionsCache.set(imageUrl, dimensions);
      resolve(dimensions);
    };
    img.onerror = () => {
      // Use default dimensions if image fails to load
      const defaultDimensions = {
        width: 400,
        height: 300,
        aspectRatio: 4 / 3
      };
      imageDimensionsCache.set(imageUrl, defaultDimensions);
      resolve(defaultDimensions);
    };
    img.src = imageUrl;
  });
}

/**
 * Preload next images with their dimensions for smooth transitions.
 */
async function preloadNextImages(currentIndex, count = 2) {
  if ((!isAuthenticated && !offlineMode) || imageRoute.length === 0) {
    return;
  }
  
  const promises = [];
  for (let i = 1; i <= count; i++) {
    const nextIndex = currentIndex + i;
    if (nextIndex < imageRoute.length) {
      const img = imageRoute[nextIndex];
      if (img && img.storagePath) {
        promises.push(
          ensureImageUrls(img).then(async () => {
            if (img.path) {
              await preloadImageAndGetDimensions(img.path);
            }
          }).catch(err => {
            console.warn(`Failed to preload image ${nextIndex}:`, err);
          })
        );
      }
    }
  }
  
  await Promise.all(promises);
}

// ============================================================================
// Route & Markers Setup
// ============================================================================

async function setupRoute() {
  if (imageRoute.length === 0) {
    return;
  }
  
  // Clear cluster icon cache when route is reset
  clusterIconCache.clear();
  
  const latlngs = imageRoute.map(img => img.coords);
  
  // Create route polyline
  if (routeLine) {
    map.removeLayer(routeLine);
  }
  routeLine = L.polyline(latlngs, { color: '#bf8013ff', weight: 3 }).addTo(map);
  map.fitBounds(routeLine.getBounds().pad(0.2));

  // Create image location markers for route animation with invisible markers
  cityMarkers.forEach(marker => map.removeLayer(marker));
  cityMarkers = imageRoute.map(img => {
    // Use DivIcon with empty HTML for truly invisible marker (works in all browsers)
    const emptyIcon = L.divIcon({
      className: 'invisible-marker',
      html: '',
      iconSize: [0, 0],
      iconAnchor: [0, 0]
    });
    
    const marker = L.marker(img.coords, { 
      icon: emptyIcon,
      keyboard: false, // Disable keyboard interaction for invisible markers
      title: '' // Remove default tooltip
    });
    
    // Popup content will be set dynamically in openCityPopup
    // Use a placeholder for now
    marker.bindPopup('', {
      className: 'single-image-popup'
    });
    marker.addTo(map);
    return marker;
  });
  
  // Setup marker clustering for manual interaction (now async)
  await setupMarkerClustering();
}

// ============================================================================
// Marker Clustering Setup
// ============================================================================

async function setupMarkerClustering() {
  // Remove existing cluster group if it exists
  if (markerClusterGroup) {
    map.removeLayer(markerClusterGroup);
    markerClusterGroup = null;
  }
  
  // Preload thumbnail URLs for markers (needed for synchronous icon creation)
  console.log('Preloading thumbnail URLs for markers...');
  const thumbnailPromises = imageRoute.map((img, index) => {
    return ensureImageUrls(img).catch(err => {
      console.warn(`Failed to load URLs for image ${index}:`, err);
      return img;
    });
  });
  await Promise.all(thumbnailPromises);
  console.log('Thumbnail URLs preloaded');
  
  // Create new marker cluster group with optimized settings for performance
  markerClusterGroup = L.markerClusterGroup({
    maxClusterRadius: 50, // Cluster markers within 50 pixels
    spiderfyOnMaxZoom: true,
    showCoverageOnHover: false,
    zoomToBoundsOnClick: false, // Disable zoom - we'll handle clicks with gallery
    animate: false, // Disable animations for better performance
    chunkedLoading: false, // Disable chunked loading to ensure clusters render immediately
    removeOutsideVisibleBounds: false, // Keep all markers to ensure clusters render on init
    iconCreateFunction: function(cluster) {
      const children = cluster.getAllChildMarkers();
      const count = cluster.getChildCount();
      
      // Create cache key based on cluster composition
      // Use first few marker thumbnails to create unique key
      const cacheKey = count + '_' + children.slice(0, 3).map(m => {
        return (m.options && m.options.thumb) ? m.options.thumb : '';
      }).join('_');
      
      // Check cache first to avoid re-rendering
      if (clusterIconCache.has(cacheKey)) {
        return clusterIconCache.get(cacheKey);
      }
      
      // Get thumbnail from first marker (or use default if not available)
      const thumb = (children[0] && children[0].options && children[0].options.thumb) || null;
      
      // Determine size based on count - increased sizes for better visibility
      const sizePx = count < 10 ? 60 : count < 100 ? 75 : 90;
      
      let icon;
      
      // If we have a thumbnail, use it; otherwise use default cluster icon
      if (thumb) {
        // Use lazy loading: create img element that loads on demand
        icon = L.divIcon({
          html: `
            <div class="cluster-thumb" style="
              position: relative;
              width: ${sizePx}px;
              height: ${sizePx}px;
              border-radius: 4px;
              overflow: hidden;
              border: 2px solid white;
              box-shadow: 0 2px 8px rgba(0,0,0,0.3);
              background-color: #ccc;
            ">
              <img src="${thumb}" 
                   alt="Cluster" 
                   loading="lazy"
                   decoding="async"
                   style="width: 100%; height: 100%; object-fit: cover;"
                   onerror="this.style.display='none'; this.parentElement.style.backgroundColor='#93c5fd';">
              <span class="count" style="
                position: absolute;
                bottom: 0;
                right: 0;
                background: rgba(0,0,0,0.7);
                color: white;
                border-radius: 3px 0 4px 0;
                padding: 2px 6px;
                font-size: 11px;
                font-weight: bold;
                line-height: 1.2;
              ">${count}</span>
            </div>
          `,
          className: 'custom-cluster-icon',
          iconSize: [sizePx, sizePx],
          iconAnchor: [sizePx / 2, sizePx / 2]
        });
      } else {
        // Fallback to default cluster icon if no thumbnail available
        const size = count < 10 ? 'small' : count < 100 ? 'medium' : 'large';
        icon = new L.DivIcon({
          html: '<div><span>' + count + '</span></div>',
          className: 'marker-cluster marker-cluster-' + size,
          iconSize: new L.Point(40, 40)
        });
      }
      
      // Cache the icon for future use
      clusterIconCache.set(cacheKey, icon);
      return icon;
    }
  });
  
  // Create clickable markers for each image with thumbnail icons
  clusterMarkers = imageRoute.map((img, index) => {
    // Ensure we have thumbnail URL (should be preloaded, but fallback just in case)
    const thumbnailUrl = img.thumbnail || '';
    
    // Use thumbnail for marker icon (much smaller file size)
    const icon = L.icon({
      iconUrl: thumbnailUrl,
      iconSize: [48, 48],
      iconAnchor: [24, 24],
      popupAnchor: [0, -24],
      className: 'image-thumbnail-marker'
    });
    
    // Create marker with thumbnail icon and store thumb path for cluster icon creation
    const marker = L.marker(img.coords, {
      icon: icon,
      thumb: thumbnailUrl  // Store thumbnail URL for cluster icon creation
    });
    
    // Store the image index and data with the marker
    marker._imageIndex = index;
    marker._imageData = img;
    
    // Add click handler to show popup when manually clicked
    marker.on('click', () => {
      // Use the same popup function but with force=true and isUserInitiated=true
      openCityPopup(index, true, true);
    });
    
    return marker;
  });
  
  // Add all markers to the cluster group BEFORE adding to map
  // This ensures proper initialization order
  clusterMarkers.forEach(marker => markerClusterGroup.addLayer(marker));
  
  // Add click handler for clusters BEFORE adding to map
  // Handle cluster clicks to show gallery popup
  markerClusterGroup.on('clusterclick', function(e) {
    const cluster = e.layer;
    
    // Get all markers in this cluster
    const markers = cluster.getAllChildMarkers();
    const imageData = markers.map(m => {
      if (m._imageData) {
        return m._imageData;
      }
      // Fallback: try to get data from marker options or route
      const index = m._imageIndex;
      if (index !== undefined && index >= 0 && index < imageRoute.length) {
        return imageRoute[index];
      }
      return null;
    }).filter(Boolean);
    
    if (imageData.length > 0) {
      // Open gallery popup
      openClusterGallery(cluster.getLatLng(), imageData);
    }
  });
  
  // Also handle clicks on the cluster group itself (fallback)
  markerClusterGroup.on('click', function(e) {
    // Check if this is a cluster (has getAllChildMarkers method)
    if (e.layer && typeof e.layer.getAllChildMarkers === 'function') {
      const cluster = e.layer;
      const markers = cluster.getAllChildMarkers();
      
      // Only handle if it's actually a cluster (more than 1 marker)
      if (markers.length > 1) {
        const imageData = markers.map(m => m._imageData).filter(Boolean);
        if (imageData.length > 0) {
          openClusterGallery(cluster.getLatLng(), imageData);
        }
      }
    }
  });
  
  // Add cluster group to map
  markerClusterGroup.addTo(map);
  
  // Trigger cluster refresh by doing a tiny zoom in/out
  // This ensures clusters render properly after fitBounds
  map.once('moveend', () => {
    // After fitBounds completes, do a tiny zoom to trigger cluster rendering
    const currentZoom = map.getZoom();
    map.setZoom(currentZoom + 0.01, { animate: false });
    setTimeout(() => {
      map.setZoom(currentZoom, { animate: false });
    }, 50);
  });
}

// ============================================================================
// Duration Calculation
// ============================================================================

function computeSegmentDurations(latlngsInput, basePerLegMs) {
  const segments = latlngsInput.length - 1;
  const totalDuration = basePerLegMs * segments;
  const distances = [];
  for (let i = 0; i < segments; i += 1) {
    const a = L.latLng(latlngsInput[i]);
    const b = L.latLng(latlngsInput[i + 1]);
    distances.push(a.distanceTo(b));
  }
  const totalDistance = distances.reduce((sum, d) => sum + d, 0);
  return distances.map(d => (d / totalDistance) * totalDuration);
}

// ============================================================================
// Popup Management
// ============================================================================

function detectArrival(position) {
  // Don't check for new arrivals if a popup is currently showing
  // Use popupIsShowing flag which is set via popupopen event (more reliable than isOpen())
  if (popupIsShowing) {
    return null;
  }
  
  // Determine the next image index to check
  // If lastArrivedIndex is -1 or null, check index 0, otherwise check next index
  const nextIndex = (lastArrivedIndex === null || lastArrivedIndex === -1) ? 0 : lastArrivedIndex + 1;
  
  // If we've already reached the last image, don't check further
  if (nextIndex >= imageRoute.length) {
    return null;
  }
  
  // Only check distance to the next image in the sequence
  const dist = L.latLng(position).distanceTo(imageRoute[nextIndex].coords);
  if (dist <= arrivalThresholdMeters) {
    lastArrivedIndex = nextIndex;
    return nextIndex;
  }
  
  return null;
}

async function openCityPopup(index, force = false, isUserInitiated = false) {
  if (index == null || !cityMarkers[index]) {
    return;
  }
  
  // Don't open a new popup if one is currently showing (unless forced)
  // Use popupIsShowing flag which is more reliable than isOpen() in Firefox
  if (!force && popupIsShowing) {
    return;
  }
  
  clearPopupTimeout();
  closeCurrentPopup();

  const marker = cityMarkers[index];
  const img = imageRoute[index];

  // Preload next images for smooth playback
  if (!isUserInitiated) {
    preloadNextImages(index, 2).catch(err => {
      console.warn('Failed to preload next images:', err);
    });
  }
  
  // Create styled popup content (async - needs to load signed URL)
  const popupContent = await createSingleImagePopupContent(img, isUserInitiated);
  
  // Update marker popup with new content
  marker.setPopupContent(popupContent);
  currentPopupMarker = marker;
  
  // Helper function to set up popup state and timeout
  // This will be called when popup actually opens (via popupopen event)
  const setupPopupState = () => {
    popupIsShowing = true;
    // Only auto-close if it's from playback (not user-initiated)
    if (!isUserInitiated) {
      popupTimeoutId = window.setTimeout(() => {
        closeCurrentPopup();
        popupIsShowing = false; // Allow next popup to be shown after duration
      }, popUpDurationMs); //popUpDurationMs
    }
  };
  
  // Use marker's popupopen event (more reliable than popup's open event)
  // Remove any existing listeners to avoid duplicates
  marker.off('popupopen');
  marker.off('popupclose');
  
  // Attach popupopen listener BEFORE opening to catch synchronous opens (Safari)
  // This event fires when popup is actually visible, handling Firefox async behavior
  marker.on('popupopen', setupPopupState);
  
  // Also handle popupclose to reset flag (safety measure)
  marker.on('popupclose', () => {
    popupIsShowing = false;
  });
  
  // Open popup - this may be synchronous (Safari) or asynchronous (Firefox)
  // The popupopen event will fire when popup is actually visible
  try {
    marker.openPopup();
  } catch (error) {
    console.error('[openCityPopup] Error opening popup:', error);
    // If openPopup fails, try to set state anyway after a delay
    setTimeout(() => {
      if (currentPopupMarker === marker) {
        setupPopupState();
      }
    }, 100);
    return;
  }
  
  // Fallback for Firefox: Check if popup actually opened by checking DOM
  // Firefox may not fire popupopen event reliably, so we need to verify
  const checkPopupOpened = () => {
    if (!popupIsShowing && currentPopupMarker === marker) {
      const popup = marker.getPopup();
      if (popup) {
        // Check both isOpen() and if popup element is in DOM
        const popupElement = popup.getElement();
        const isActuallyOpen = popup.isOpen() || (popupElement && popupElement.parentElement);
        
        if (isActuallyOpen) {
          setupPopupState();
        }
      }
    }
  };
  
  // Check immediately (for Safari synchronous case) and after delay (for Firefox async case)
  // Use requestAnimationFrame for better timing
  requestAnimationFrame(() => {
    checkPopupOpened();
    // Also check after a delay for Firefox async behavior
    setTimeout(checkPopupOpened, 50);
  });
}

function closeCurrentPopup() {
  if (currentPopupMarker) {
    // Remove event listeners before closing to avoid triggering them
    currentPopupMarker.off('popupopen');
    currentPopupMarker.off('popupclose');
    currentPopupMarker.closePopup();
    currentPopupMarker = null;
  }
  clearPopupTimeout();
  popupIsShowing = false; // Reset flag when popup is closed
}

function clearPopupTimeout() {
  if (popupTimeoutId) {
    window.clearTimeout(popupTimeoutId);
    popupTimeoutId = null;
  }
}

// ============================================================================
// Cluster Gallery Popup
// ============================================================================

let clusterGalleryPopup = null;
let clusterGalleryCurrentIndex = 0;
let clusterGalleryImages = [];
let clusterGalleryKeyHandler = null;
let clusterGalleryTouchStartX = null;
let clusterGalleryTouchStartY = null;
const SWIPE_THRESHOLD = 50; // Minimum distance in pixels to trigger swipe

async function openClusterGallery(latlng, imageData) {
  if (!imageData || imageData.length === 0) {
    return;
  }
  
  // Close any existing popups first
  closeCurrentPopup();
  if (clusterGalleryPopup) {
    map.removeLayer(clusterGalleryPopup);
    clusterGalleryPopup = null;
  }
  
  // Remove existing keyboard listener if any
  if (clusterGalleryKeyHandler) {
    document.removeEventListener('keydown', clusterGalleryKeyHandler);
    clusterGalleryKeyHandler = null;
  }
  
  clusterGalleryImages = imageData;
  clusterGalleryCurrentIndex = 0;
  
  // Preload first image and next image for smooth navigation
  if (imageData.length > 0) {
    await ensureImageUrls(imageData[0]);
    if (imageData[0].path) {
      await preloadImageAndGetDimensions(imageData[0].path);
    }
    if (imageData.length > 1) {
      await ensureImageUrls(imageData[1]);
      if (imageData[1].path) {
        await preloadImageAndGetDimensions(imageData[1].path);
      }
    }
  }
  
  // Create popup content with gallery (async - needs to load signed URL)
  const popupContent = await createClusterGalleryContent();
  
  if (!popupContent) {
    return;
  }
  
  // Create a temporary marker for the popup (we'll remove it after popup closes)
  const tempMarker = L.marker(latlng, {
    icon: L.divIcon({
      className: 'cluster-gallery-marker',
      iconSize: [1, 1],
      html: ''
    })
  });
  
  tempMarker.bindPopup(popupContent, {
    maxWidth: 480,
    minWidth: 400,
    className: 'cluster-gallery-popup',
    closeOnClick: false,
    autoPan: true
  });
  
  tempMarker.addTo(map);
  tempMarker.openPopup();
  
  clusterGalleryPopup = tempMarker;
  
  // Add keyboard event handler for arrow keys
  clusterGalleryKeyHandler = (e) => {
    if (!clusterGalleryPopup || !map.hasLayer(clusterGalleryPopup)) {
      return;
    }
    
    // Check if popup is open
    const popup = clusterGalleryPopup.getPopup();
    if (!popup || !popup.isOpen()) {
      return;
    }
    
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      // Prevent map navigation when gallery is open
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      
      if (e.key === 'ArrowLeft') {
        navigateClusterGallery(-1);
      } else {
        navigateClusterGallery(1);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeClusterGallery();
    }
  };
  
  // Use capture phase to intercept arrow keys before they reach map
  document.addEventListener('keydown', clusterGalleryKeyHandler, true);
  
  // Add touch event handlers for swipe gestures (mobile)
  const popupElement = tempMarker.getPopup().getElement();
  if (popupElement) {
    const handleTouchStart = (e) => {
      const touch = e.touches[0];
      clusterGalleryTouchStartX = touch.clientX;
      clusterGalleryTouchStartY = touch.clientY;
    };
    
    const handleTouchMove = (e) => {
      // Prevent default scrolling while swiping
      if (clusterGalleryTouchStartX !== null) {
        e.preventDefault();
      }
    };
    
    const handleTouchEnd = (e) => {
      if (clusterGalleryTouchStartX === null || clusterGalleryTouchStartY === null) {
        return;
      }
      
      const touch = e.changedTouches[0];
      const deltaX = touch.clientX - clusterGalleryTouchStartX;
      const deltaY = touch.clientY - clusterGalleryTouchStartY;
      const absDeltaX = Math.abs(deltaX);
      const absDeltaY = Math.abs(deltaY);
      
      // Check if it's a horizontal swipe (more horizontal than vertical)
      if (absDeltaX > absDeltaY && absDeltaX > SWIPE_THRESHOLD) {
        if (deltaX > 0) {
          // Swipe right - go to previous image
          navigateClusterGallery(-1);
        } else {
          // Swipe left - go to next image
          navigateClusterGallery(1);
        }
      }
      
      // Reset touch start
      clusterGalleryTouchStartX = null;
      clusterGalleryTouchStartY = null;
    };
    
    popupElement.addEventListener('touchstart', handleTouchStart, { passive: false });
    popupElement.addEventListener('touchmove', handleTouchMove, { passive: false });
    popupElement.addEventListener('touchend', handleTouchEnd, { passive: true });
    
    // Store handlers for cleanup
    tempMarker._touchHandlers = {
      element: popupElement,
      start: handleTouchStart,
      move: handleTouchMove,
      end: handleTouchEnd
    };
  }
  
  // Don't auto-close on map clicks - let users click navigation buttons
  // Users can close with Escape key or clicking outside is handled by Leaflet's default behavior
  
  // Clean up marker and keyboard handler when popup closes
  tempMarker.on('popupclose', () => {
    if (map.hasLayer(tempMarker)) {
      map.removeLayer(tempMarker);
    }
    if (clusterGalleryKeyHandler) {
      document.removeEventListener('keydown', clusterGalleryKeyHandler, true);
      clusterGalleryKeyHandler = null;
    }
    // Clean up touch handlers
    if (tempMarker._touchHandlers) {
      const { element, start, move, end } = tempMarker._touchHandlers;
      element.removeEventListener('touchstart', start);
      element.removeEventListener('touchmove', move);
      element.removeEventListener('touchend', end);
      tempMarker._touchHandlers = null;
    }
    clusterGalleryPopup = null;
  });
}

async function createClusterGalleryContent() {
  if (clusterGalleryImages.length === 0) return '';
  
  const currentImage = clusterGalleryImages[clusterGalleryCurrentIndex];
  const totalImages = clusterGalleryImages.length;
  const hasNext = clusterGalleryCurrentIndex < totalImages - 1;
  const hasPrev = clusterGalleryCurrentIndex > 0;
  
  // Ensure we have the image URL (load if needed)
  await ensureImageUrls(currentImage);
  const imageUrl = currentImage.path || '';
  
  // Preload next image in cluster for smooth navigation
  if (hasNext) {
    const nextImage = clusterGalleryImages[clusterGalleryCurrentIndex + 1];
    if (nextImage) {
      ensureImageUrls(nextImage).then(async () => {
        if (nextImage.path) {
          await preloadImageAndGetDimensions(nextImage.path);
        }
      }).catch(err => {
        console.warn('Failed to preload next cluster image:', err);
      });
    }
  }
  
  // Preload current image to ensure it's ready (for smooth display)
  await preloadImageAndGetDimensions(imageUrl);
  
  // Find the image index in the main route for proper popup opening
  const routeIndex = imageRoute.findIndex(img => 
    img.name === currentImage.name || 
    (img.coords[0] === currentImage.coords[0] && img.coords[1] === currentImage.coords[1])
  );
  
  return `
    <div class="cluster-gallery-container" style="display: flex; flex-direction: column; align-items: center; text-align: center;">
      <div class="cluster-gallery-header" style="width: 100%; text-align: center;" >
        <span class="cluster-gallery-count" style="display: flex; align-items: center; justify-content: center;">Showing image ${clusterGalleryCurrentIndex + 1} of ${totalImages}</span>
      </div>
       <div class="cluster-gallery-image-wrapper">
         <button class="cluster-gallery-nav cluster-gallery-prev" 
                 ${!hasPrev ? 'disabled' : ''} 
                 onclick="navigateClusterGallery(-1)"
                 title="Previous image (or press Left Arrow)">
           ‹
         </button>
         <div class="cluster-gallery-image">
           <img src="${imageUrl}" 
                alt="${currentImage.name}" 
                loading="eager"
                decoding="async"
                onclick="openImageFromCluster(${routeIndex !== -1 ? routeIndex : 0})"
                style="width: auto; height: auto; border-radius: 4px; cursor: pointer; object-fit: contain;"
                title="Click to view full size">
         </div>
         <button class="cluster-gallery-nav cluster-gallery-next" 
                 ${!hasNext ? 'disabled' : ''} 
                 onclick="navigateClusterGallery(1)"
                 title="Next image (or press Right Arrow)">
           ›
         </button>
       </div>
      <div class="cluster-gallery-info" style="display: flex; align-items: center; justify-content: center;">
        ${currentImage.date ? `<span>Date: ${currentImage.date}</span>` : ''}
        ${currentImage.timestamp ? `<span>Time: ${new Date(currentImage.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>` : ''}
      </div>
      <div class="cluster-gallery-swipe-hint">
        ← Swipe to see other images →
      </div>
    </div>
  `;
}

// Global functions for popup button clicks (called from HTML)
window.closeClusterGallery = function() {
  if (clusterGalleryPopup) {
    clusterGalleryPopup.closePopup();
  }
  // Remove keyboard handler when closing
  if (clusterGalleryKeyHandler) {
    document.removeEventListener('keydown', clusterGalleryKeyHandler, true);
    clusterGalleryKeyHandler = null;
  }
};

// Create popup content for single image (used for both playback and user clicks)
async function createSingleImagePopupContent(img, isUserInitiated = false, previousImageUrl = null) {
  // Ensure we have the image URL (load if needed)
  await ensureImageUrls(img);
  const imageUrl = img.path || '';
  
  // Preload current image to ensure it's ready (for smooth display)
  await preloadImageAndGetDimensions(imageUrl);
  
  return `
    <div class="single-image-popup-container">
      <div class="single-image-popup-image-wrapper">
        <div class="single-image-popup-image">
          <img src="${imageUrl}" 
               alt="${img.name}" 
               loading="eager"
               decoding="async"
               onclick="window.open('${imageUrl}', '_blank')"
               style="width: auto; height: auto; border-radius: 4px; cursor: pointer; object-fit: contain; max-width: 100%; max-height: 450px;"
               title="Click to view full size">
        </div>
      </div>
      <div class="single-image-popup-info" style="display: flex; align-items: center; justify-content: center;">
        ${img.date ? `<span>Date: ${img.date}</span>` : ''}
        ${img.timestamp ? `<span>Time: ${new Date(img.timestamp).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>` : ''}
      </div>
    </div>
  `;
}

window.navigateClusterGallery = async function(direction) {
  clusterGalleryCurrentIndex += direction;
  if (clusterGalleryCurrentIndex < 0) {
    clusterGalleryCurrentIndex = clusterGalleryImages.length - 1;
  } else if (clusterGalleryCurrentIndex >= clusterGalleryImages.length) {
    clusterGalleryCurrentIndex = 0;
  }
  
  if (clusterGalleryPopup) {
    const newContent = await createClusterGalleryContent();
    clusterGalleryPopup.setPopupContent(newContent);
  }
};


window.openImageFromCluster = function(index) {
  if (index >= 0 && index < imageRoute.length) {
    closeClusterGallery();
    openCityPopup(index, true, true); // User-initiated when clicking from cluster
  }
};

// ============================================================================
// Moving Marker Management
// ============================================================================

function attachEvents(marker) {
  marker.on('start', async () => {
    updateButtonState('playing');
    lastArrivedIndex = -1; // Reset to -1 so next check will be index 0
    
    // Open popup for index 0
    const marker0 = cityMarkers[0];
    if (marker0) {
      marker0.once('popupopen', () => {
        lastArrivedIndex = 0;
      });
    }
    
    await openCityPopup(0, true); // Force open
    
    // Fallback check for synchronous open
    if (marker0) {
      const popup = marker0.getPopup();
      if (popup && popup.isOpen() && lastArrivedIndex === -1) {
        lastArrivedIndex = 0;
      }
    }
  });

  marker.on('end', async (e) => {
    await openCityPopup(imageRoute.length - 1, true); // Show final image
    
    // Loop: restart the animation (this will fire 'start' and handle reset/popups)
    marker.start(); // No closeCurrentPopup() or lastArrivedIndex reset needed—'start' handles it
    // Do NOT call updateButtonState('ended')—keep 'playing' state for continuous loop
  });

  marker.on('move', async (e) => {
    const idx = detectArrival(e.latlng);
    if (idx !== null) {
      await openCityPopup(idx);
    }
  });
}

function createMovingMarker() {
  if (movingMarker) {
    map.removeLayer(movingMarker);
  }

  if (imageRoute.length === 0) {
    return null;
  }

  const latlngs = imageRoute.map(img => img.coords);
  const durations = computeSegmentDurations(latlngs, baseLegDurationMs);
  movingMarker = L.Marker.movingMarker(latlngs, durations, {
    autostart: false,
    loop: false // Change to false
  });

  // Add station dwells at each intermediate image location
  for (let i = 1; i < latlngs.length - 1; i += 1) {
    movingMarker.addStation(i, stationDurationMs);
  }

  attachEvents(movingMarker);
  movingMarker.addTo(map);
  return movingMarker;
}

function ensureMarker() {
  if (!movingMarker) {
    createMovingMarker();
  }
  return movingMarker;
}

// ============================================================================
// UI Controls
// ============================================================================

function updateButtonState(state) {
  if (!playPauseBtn) return;
  
  switch (state) {
    case 'playing':
      isPlaying = true;
      isPaused = false;
      // Show pause icon, hide play icon
      if (playIcon) playIcon.style.display = 'none';
      if (pauseIcon) pauseIcon.style.display = 'block';
      playPauseBtn.setAttribute('aria-label', 'Pause');
      break;
    case 'paused':
      isPlaying = false;
      isPaused = true;
      // Show play icon, hide pause icon
      if (playIcon) playIcon.style.display = 'block';
      if (pauseIcon) pauseIcon.style.display = 'none';
      playPauseBtn.setAttribute('aria-label', 'Resume');
      break;
    case 'stopped':
    case 'ended':
    case 'idle':
    default:
      isPlaying = false;
      isPaused = false;
      // Show play icon, hide pause icon
      if (playIcon) playIcon.style.display = 'block';
      if (pauseIcon) pauseIcon.style.display = 'none';
      playPauseBtn.setAttribute('aria-label', 'Play');
      break;
  }
}

// ============================================================================
// Event Listeners - New video-player-style controls
// ============================================================================

// Play/Pause toggle button
if (playPauseBtn) {
  playPauseBtn.addEventListener('click', () => {
    const marker = ensureMarker();
    
    if (!isPlaying && !isPaused) {
      // Start playing (from idle or ended state)
      marker.options.loop = false; // Always loop
      marker.start();
      updateButtonState('playing');
    } else if (isPlaying) {
      // Pause
      marker.pause();
      updateButtonState('paused');
    } else if (isPaused) {
      // Resume
      marker.resume();
      updateButtonState('playing');
    }
  });
}


// ============================================================================
// Initialization
// ============================================================================

async function init() {
  // If offline mode, skip authentication and start app immediately
  if (offlineMode) {
    isAuthenticated = true;
    showContent();
    // Hide logout button in offline mode (no authentication available)
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) logoutBtn.style.display = 'none';
    window.handleLogout = handleLogout; // no-op in offline
    await initializeApp();
    return;
  }

  // Initialize Supabase first
  if (!initializeSupabase()) {
    showError('Failed to initialize authentication. Please refresh the page.');
    return;
  }

  // Check for existing session
  const hasSession = await checkSession();
  // Ensure logout button visibility matches mode: hide in offline, show in online
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) logoutBtn.style.display = (offlineMode ? 'none' : 'block');

  // Set up auth state listener
  if (supabaseClient) {
    supabaseClient.auth.onAuthStateChange(async (event, session) => {
      if (event === 'SIGNED_IN' && session) {
        isAuthenticated = true;
        showContent();
        // Initialize the app if not already initialized
        if (!map) {
          await initializeApp();
        }
      } else if (event === 'SIGNED_OUT') {
        isAuthenticated = false;
        showLogin();
      }
    });
  }
  
  // Set up login form handler
  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      hideError();

      const passwordInput = document.getElementById('password');
      const loginBtn = document.getElementById('loginBtn');

      if (!passwordInput) {
        showError('Password field not found.');
        return;
      }

      const password = passwordInput.value;

      if (!password) {
        showError('Please enter password.');
        return;
      }

      // Disable button during login
      if (loginBtn) {
        loginBtn.disabled = true;
        loginBtn.textContent = 'Logging in...';
      }

      const success = await handleLogin(SHARED_EMAIL, password);

      // Re-enable button
      if (loginBtn) {
        loginBtn.disabled = false;
        loginBtn.textContent = 'Submit';
      }

      if (success) {
        // Clear form
        passwordInput.value = '';
        hideError();
      }
    });
  }

  // Make handleLogout globally available
  window.handleLogout = handleLogout;

  // Only initialize map if authenticated
  if (hasSession) {
    await initializeApp();
  }
}

async function initializeApp() {
  initializeMap();
  
  // Load image data first
  await loadImageData();
  
  if (imageRoute.length === 0) {
    return;
  }
  
  await setupRoute();
  createMovingMarker();
  updateButtonState('idle');
  lastArrivedIndex = -1; // Initialize to -1 so next check will be index 0
  
  // Preload first few images and their dimensions for smooth playback
  preloadNextImages(0, 3).catch(err => {
    console.warn('Failed to preload initial images:', err);
  });
  
  await openCityPopup(0, true); // Force to show first popup
  lastArrivedIndex = 0; // Set to 0 after showing first popup
}

// Start when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}


