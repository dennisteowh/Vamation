// Utility Functions for VAMA Gallery

/**
 * Utility functions for common operations
 */
const Utils = {
    
    // Debounce function for search and other frequent operations
    debounce(func, wait, immediate = false) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                timeout = null;
                if (!immediate) func(...args);
            };
            const callNow = immediate && !timeout;
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
            if (callNow) func(...args);
        };
    },

    // Throttle function for scroll and resize events
    throttle(func, limit) {
        let inThrottle;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    },

    // Format date strings
    formatDate(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        const diffTime = Math.abs(now - date);
        const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

        if (diffDays === 1) {
            return 'Yesterday';
        } else if (diffDays < 7) {
            return `${diffDays} days ago`;
        } else if (diffDays < 30) {
            return `${Math.floor(diffDays / 7)} weeks ago`;
        } else {
            return date.toLocaleDateString('en-US', {
                year: 'numeric',
                month: 'short',
                day: 'numeric'
            });
        }
    },

    // Format file sizes
    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },

    // Sanitize HTML to prevent XSS
    sanitizeHtml(str) {
        const temp = document.createElement('div');
        temp.textContent = str;
        return temp.innerHTML;
    },

    // Generate unique IDs
    generateId(prefix = 'id') {
        return `${prefix}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    },

    // Cookie management
    cookie: {
        set(name, value, days = 365) {
            const expires = new Date();
            expires.setTime(expires.getTime() + (days * 24 * 60 * 60 * 1000));
            document.cookie = `${name}=${encodeURIComponent(value)};expires=${expires.toUTCString()};path=/`;
        },

        get(name) {
            const nameEQ = name + '=';
            const ca = document.cookie.split(';');
            for (let c of ca) {
                while (c.charAt(0) === ' ') c = c.substring(1, c.length);
                if (c.indexOf(nameEQ) === 0) {
                    return decodeURIComponent(c.substring(nameEQ.length, c.length));
                }
            }
            return null;
        },

        remove(name) {
            document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;`;
        }
    },

    // Local storage with JSON support
    storage: {
        set(key, value) {
            try {
                localStorage.setItem(key, JSON.stringify(value));
                return true;
            } catch (e) {
                console.warn('Storage failed:', e);
                return false;
            }
        },

        get(key, defaultValue = null) {
            try {
                const item = localStorage.getItem(key);
                return item ? JSON.parse(item) : defaultValue;
            } catch (e) {
                console.warn('Storage retrieval failed:', e);
                return defaultValue;
            }
        },

        remove(key) {
            try {
                localStorage.removeItem(key);
                return true;
            } catch (e) {
                console.warn('Storage removal failed:', e);
                return false;
            }
        }
    },

    // URL handling
    url: {
        getParams() {
            const params = {};
            const urlParams = new URLSearchParams(window.location.search);
            for (const [key, value] of urlParams) {
                params[key] = value;
            }
            return params;
        },

        updateParam(key, value, pushState = false) {
            const url = new URL(window.location);
            if (value === null || value === undefined || value === '') {
                url.searchParams.delete(key);
            } else {
                url.searchParams.set(key, value);
            }
            
            if (pushState) {
                window.history.pushState({}, '', url);
            } else {
                window.history.replaceState({}, '', url);
            }
        }
    },

    // Array utilities
    array: {
        chunk(array, size) {
            const chunks = [];
            for (let i = 0; i < array.length; i += size) {
                chunks.push(array.slice(i, i + size));
            }
            return chunks;
        },

        shuffle(array) {
            const shuffled = [...array];
            for (let i = shuffled.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
            }
            return shuffled;
        },

        unique(array, key = null) {
            if (key) {
                return array.filter((item, index, self) => 
                    self.findIndex(i => i[key] === item[key]) === index
                );
            }
            return [...new Set(array)];
        },

        sortBy(array, key, order = 'asc') {
            return array.sort((a, b) => {
                let aVal = key ? a[key] : a;
                let bVal = key ? b[key] : b;
                
                // Handle dates
                if (typeof aVal === 'string' && aVal.match(/^\d{4}-\d{2}-\d{2}/)) {
                    aVal = new Date(aVal);
                    bVal = new Date(bVal);
                }
                
                // Handle strings (case-insensitive)
                if (typeof aVal === 'string' && typeof bVal === 'string') {
                    aVal = aVal.toLowerCase();
                    bVal = bVal.toLowerCase();
                }
                
                if (aVal < bVal) return order === 'asc' ? -1 : 1;
                if (aVal > bVal) return order === 'asc' ? 1 : -1;
                return 0;
            });
        }
    },

    // DOM utilities
    dom: {
        createElement(tag, className, attributes = {}) {
            const element = document.createElement(tag);
            if (className) element.className = className;
            
            Object.entries(attributes).forEach(([key, value]) => {
                if (key === 'innerHTML') {
                    element.innerHTML = value;
                } else if (key === 'textContent') {
                    element.textContent = value;
                } else if (key === 'disabled' || key === 'checked' || key === 'selected') {
                    // Handle boolean properties directly
                    element[key] = value;
                } else {
                    element.setAttribute(key, value);
                }
            });
            
            return element;
        },

        removeElement(selector) {
            const element = typeof selector === 'string' ? 
                document.querySelector(selector) : selector;
            if (element && element.parentNode) {
                element.parentNode.removeChild(element);
            }
        },

        addEventListeners(element, events) {
            Object.entries(events).forEach(([event, handler]) => {
                element.addEventListener(event, handler);
            });
        },

        hasClass(element, className) {
            return element.classList.contains(className);
        },

        toggleClass(element, className, force = null) {
            if (force !== null) {
                element.classList.toggle(className, force);
            } else {
                element.classList.toggle(className);
            }
        }
    },

    // Image utilities
    image: {
        preload(src) {
            return new Promise((resolve, reject) => {
                const img = new Image();
                img.onload = () => resolve(img);
                img.onerror = () => reject(new Error(`Failed to load image: ${src}`));
                img.src = src;
            });
        },

        getImagePath(postId, filename = null, type = 'profile') {
            const baseUrl = '/api/image/';
            if (type === 'profile') {
                return `${baseUrl}profile/${postId}`;
            } else if (type === 'content' && filename) {
                return `${baseUrl}content/${postId}/${encodeURIComponent(filename)}`;
            }
            return null;
        },

        createPlaceholder(width = 300, height = 200, text = 'No Image') {
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            
            // Gradient background
            const gradient = ctx.createLinearGradient(0, 0, width, height);
            gradient.addColorStop(0, '#f1f5f9');
            gradient.addColorStop(1, '#e2e8f0');
            ctx.fillStyle = gradient;
            ctx.fillRect(0, 0, width, height);
            
            // Text
            ctx.fillStyle = '#64748b';
            ctx.font = '16px Inter, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(text, width / 2, height / 2);
            
            return canvas.toDataURL();
        }
    },

    // Animation utilities
    animation: {
        fadeIn(element, duration = 300) {
            element.style.opacity = '0';
            element.style.display = 'block';
            
            let start = null;
            function animate(timestamp) {
                if (!start) start = timestamp;
                const progress = timestamp - start;
                const opacity = Math.min(progress / duration, 1);
                
                element.style.opacity = opacity;
                
                if (progress < duration) {
                    requestAnimationFrame(animate);
                }
            }
            
            requestAnimationFrame(animate);
        },

        fadeOut(element, duration = 300) {
            let start = null;
            function animate(timestamp) {
                if (!start) start = timestamp;
                const progress = timestamp - start;
                const opacity = Math.max(1 - (progress / duration), 0);
                
                element.style.opacity = opacity;
                
                if (progress < duration) {
                    requestAnimationFrame(animate);
                } else {
                    element.style.display = 'none';
                }
            }
            
            requestAnimationFrame(animate);
        },

        slideDown(element, duration = 300) {
            element.style.display = 'block';
            element.style.height = '0px';
            element.style.overflow = 'hidden';
            
            const targetHeight = element.scrollHeight;
            let start = null;
            
            function animate(timestamp) {
                if (!start) start = timestamp;
                const progress = timestamp - start;
                const height = Math.min((progress / duration) * targetHeight, targetHeight);
                
                element.style.height = height + 'px';
                
                if (progress < duration) {
                    requestAnimationFrame(animate);
                } else {
                    element.style.height = '';
                    element.style.overflow = '';
                }
            }
            
            requestAnimationFrame(animate);
        }
    },

    // Validation utilities
    validate: {
        email(email) {
            const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            return regex.test(email);
        },

        filename(filename) {
            const invalidChars = /[<>:"/\\|?*]/;
            return !invalidChars.test(filename) && filename.length > 0 && filename.length <= 255;
        },

        postName(name) {
            return name && name.trim().length > 0 && name.length <= 500;
        }
    }
};

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Utils;
}

// Global error handler
window.addEventListener('error', (event) => {
    console.error('Global error:', event.error);
    // Could send to analytics or error reporting service
});

window.addEventListener('unhandledrejection', (event) => {
    console.error('Unhandled promise rejection:', event.reason);
    event.preventDefault();
});