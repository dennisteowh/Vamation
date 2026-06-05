// API Layer for VAMA Gallery - Handles all backend communication

/**
 * API configuration and methods
 */
const API = {
    baseUrl: '/api',
    
    // Configuration
    config: {
        timeout: 120000, // 120 seconds (2 minutes)
        retryAttempts: 3,
        retryDelay: 1000 // 1 second
    },

    /**
     * Core request method with retry logic and error handling
     */
    async request(endpoint, options = {}) {
        const url = `${this.baseUrl}${endpoint}`;
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            timeout: this.config.timeout
        };

        const finalOptions = {
            ...defaultOptions,
            ...options,
            headers: {
                ...defaultOptions.headers,
                ...options.headers
            }
        };

        let lastError;
        for (let attempt = 0; attempt < this.config.retryAttempts; attempt++) {
            try {
                // Create abort controller for timeout
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), finalOptions.timeout);

                const response = await fetch(url, {
                    ...finalOptions,
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                const contentType = response.headers.get('content-type');
                if (contentType && contentType.includes('application/json')) {
                    const data = await response.json();
                    return {
                        success: true,
                        data: data,
                        status: response.status
                    };
                } else {
                    return {
                        success: true,
                        data: await response.text(),
                        status: response.status
                    };
                }

            } catch (error) {
                lastError = error;
                console.warn(`API request attempt ${attempt + 1} failed:`, error.message);
                
                if (attempt < this.config.retryAttempts - 1) {
                    await new Promise(resolve => setTimeout(resolve, this.config.retryDelay));
                }
            }
        }

        console.error('API request failed after all retries:', lastError);
        return {
            success: false,
            error: lastError.message,
            status: 0
        };
    },

    // GET request wrapper
    async get(endpoint, params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const url = queryString ? `${endpoint}?${queryString}` : endpoint;
        return this.request(url, { method: 'GET' });
    },

    // POST request wrapper
    async post(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },

    // PUT request wrapper
    async put(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'PUT',
            body: JSON.stringify(data)
        });
    },

    // DELETE request wrapper
    async delete(endpoint) {
        return this.request(endpoint, { method: 'DELETE' });
    },

    /**
     * Metadata operations
     */
    metadata: {
        // Get all posts metadata
        async getPosts(params = {}) {
            return API.get('/posts', params);
        },

        // Get single post metadata
        async getPost(postId) {
            return API.get(`/posts/${postId}`);
        },

        // Update post metadata
        async updatePost(postId, data) {
            return API.put(`/posts/${postId}`, data);
        }
    },

    /**
     * File operations
     */
    files: {
        // Download and extract files for a post
        async download(postId) {
            return API.post(`/posts/${postId}/extract`);
        },

        // Delete extracted files for a post
        async deleteExtracted(postId) {
            return API.delete(`/posts/${postId}/files`);
        }
    },

    /**
     * Image operations
     */
    images: {
        // Get profile image preview for gallery cards
        getProfileImageUrl(postId) {
            return `${API.baseUrl}/images/profile-preview/${postId}`;
        },

        // Get original profile image
        getOriginalProfileImageUrl(postId) {
            return `${API.baseUrl}/images/profile/${postId}`;
        },

        // Get content image
        getContentImageUrl(postId, filename) {
            return `${API.baseUrl}/images/content/${postId}/${encodeURIComponent(filename)}`;
        },

        // Get thumbnail
        getThumbnailUrl(postId, filename, size = 'medium') {
            return `${API.baseUrl}/images/thumbnail/${postId}/${encodeURIComponent(filename)}?size=${size}`;
        },

        // Preload image
        async preload(url) {
            return Utils.image.preload(url);
        }
    },

    /**
     * App updater operations
     */
    updater: {
        // Trigger app update
        trigger(reason = 'app-load') {
            return API.post('/update/trigger', { reason });
        },

        // Get update status
        getStatus() {
            return API.get('/update/status');
        }
    }
};

/**
 * API Response Cache
 * Simple in-memory cache for API responses to reduce server load
 */
class APICache {
    constructor(ttl = 300000) { // 5 minutes default TTL
        this.cache = new Map();
        this.ttl = ttl;
    }

    generateKey(endpoint, params) {
        return `${endpoint}_${JSON.stringify(params || {})}`;
    }

    set(endpoint, params, data) {
        const key = this.generateKey(endpoint, params);
        this.cache.set(key, {
            data,
            timestamp: Date.now()
        });
    }

    get(endpoint, params) {
        const key = this.generateKey(endpoint, params);
        const cached = this.cache.get(key);
        
        if (!cached) return null;
        
        if (Date.now() - cached.timestamp > this.ttl) {
            this.cache.delete(key);
            return null;
        }
        
        return cached.data;
    }

    clear() {
        this.cache.clear();
    }

    clearPattern(pattern) {
        for (const key of this.cache.keys()) {
            if (key.includes(pattern)) {
                this.cache.delete(key);
            }
        }
    }
}

// Create global cache instance
API.cache = new APICache();

// Add caching to GET requests
const originalGet = API.get;
API.get = async function(endpoint, params = {}, useCache = true) {
    if (useCache) {
        const cached = API.cache.get(endpoint, params);
        if (cached) {
            return {
                success: true,
                data: cached,
                cached: true
            };
        }
    }
    
    const result = await originalGet.call(this, endpoint, params);
    
    if (result.success && useCache) {
        API.cache.set(endpoint, params, result.data);
    }
    
    return result;
};

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = API;
}