// Main Application - Orchestrates all components

/**
 * Main Application Class
 */
class VamaGalleryApp {
    constructor() {
        this.version = '2.0.0'; // Increment this when making major changes
        this.gallery = null;
        this.isInitialized = false;
        this.systemStatus = {
            online: true,
            lastUpdate: null,
            errors: []
        };
        
        this.init();
    }

    async init() {
        console.log('🎨 Initializing VAMA Gallery App v' + this.version + '...');
        
        try {
            // Wait for DOM to be ready
            if (document.readyState === 'loading') {
                await new Promise(resolve => {
                    document.addEventListener('DOMContentLoaded', resolve);
                });
            }
            
            // Initialize components
            await this.initializeComponents();
            
            // Setup global event listeners
            this.setupGlobalEventListeners();
            
            // Setup error handling
            this.setupErrorHandling();
            
            // Mark as initialized
            this.isInitialized = true;
            
            console.log('✅ VAMA Gallery App initialized successfully');
            
        } catch (error) {
            console.error('❌ Failed to initialize app:', error);
            statusManager.showError('Failed to initialize application: ' + error.message);
        }
    }

    async initializeComponents() {
        console.log('🔧 Initializing components...');

        // Initialize Theme Manager
        this.initializeTheme();
        console.log('✓ Theme manager initialized');

        // Initialize PlaylistManager
        window.playlistManager = new PlaylistManager(this);
        console.log('✓ Playlist manager initialized');

        // Initialize Gallery
        this.gallery = new Gallery(this);
        console.log('✓ Gallery initialized');
        
        // Initialize Playlists
        this.initializePlaylists();
        console.log('✓ Playlists initialized');
        
        // Wait for initial data load
        if (this.gallery.isLoading) {
            await new Promise(resolve => {
                const checkLoading = () => {
                    if (!this.gallery.isLoading) {
                        resolve();
                    } else {
                        setTimeout(checkLoading, 100);
                    }
                };
                checkLoading();
            });
        }
        
        console.log('✓ All components initialized');
    }

    setupGlobalEventListeners() {
        console.log('🎧 Setting up global event listeners...');

        // Handle online/offline status
        window.addEventListener('online', () => {
            this.systemStatus.online = true;
            statusManager.showSuccess('Connection restored');
        });
        
        window.addEventListener('offline', () => {
            this.systemStatus.online = false;
            statusManager.showWarning('Connection lost - working offline');
        });
        
        // Handle visibility change (tab switching)
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') {
                this.handleTabFocus();
            }
        });
        
        // Handle window resize
        window.addEventListener('resize', 
            Utils.throttle(() => this.handleWindowResize(), 250)
        );
        
        // Manual metadata update button
        const updateMetadataBtn = document.getElementById('updateMetadataBtn');
        if (updateMetadataBtn) {
            updateMetadataBtn.addEventListener('click', async () => {
                const originalHtml = updateMetadataBtn.innerHTML;
                updateMetadataBtn.disabled = true;
                updateMetadataBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>Updating...</span>';
                try {
                    const result = await API.updater.trigger('manual-button');
                    if (result?.data?.started) {
                        statusManager.showInfo('Metadata update started in background');
                    } else if (result?.data?.already_running || result?.data?.status?.running) {
                        statusManager.showInfo('Metadata update is already running');
                    } else {
                        statusManager.showWarning('Update request was received, but did not start as expected');
                    }
                } catch (error) {
                    console.error('Failed to trigger metadata update:', error);
                    statusManager.showError('Failed to start metadata update: ' + error.message);
                } finally {
                    updateMetadataBtn.disabled = false;
                    updateMetadataBtn.innerHTML = originalHtml;
                }
            });
        }

        // Handle beforeunload (page refresh/close)
        window.addEventListener('beforeunload', (e) => {
            this.handlePageUnload(e);
        });
        
        // Global keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            this.handleGlobalKeyboard(e);
        });
        
        console.log('✓ Global event listeners set up');
    }

    setupErrorHandling() {
        // Global error handler
        window.addEventListener('error', (event) => {
            this.handleGlobalError(event.error, 'JavaScript Error', event);
        });
        
        // Promise rejection handler
        window.addEventListener('unhandledrejection', (event) => {
            this.handleGlobalError(event.reason, 'Unhandled Promise Rejection', event);
        });
        
        // API error handler
        document.addEventListener('apiError', (event) => {
            this.handleAPIError(event.detail);
        });
    }

    // Event Handlers
    handleTabFocus() {
        // Check if we need to refresh data when tab becomes visible
        const lastUpdate = Utils.storage.get('last_data_update');
        if (lastUpdate && Date.now() - lastUpdate > 300000) { // 5 minutes
            this.refreshData();
        }
    }

    handleWindowResize() {
        // Trigger resize events for components
        if (this.gallery) {
            // Gallery might need to recalculate layout
            this.gallery.render();
        }
    }

    handlePageUnload(e) {
        // Check for unsaved changes
        if (this.hasUnsavedChanges()) {
            const message = 'You have unsaved changes. Are you sure you want to leave?';
            e.returnValue = message;
            return message;
        }
    }

    handleGlobalKeyboard(e) {
        // Handle global shortcuts that work everywhere
        if (e.ctrlKey || e.metaKey) {
            switch (e.key) {
                case 'k': // Search
                    e.preventDefault();
                    this.focusSearch();
                    break;
                    
                case 'r': // Refresh
                    e.preventDefault();
                    this.refreshData();
                    break;
                    
                case 'h': // Help
                    e.preventDefault();
                    this.showHelp();
                    break;
            }
        }
        
        // Handle other global shortcuts
        if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
            e.preventDefault();
            this.showKeyboardShortcuts();
        }
    }

    handleGlobalError(error, type, event) {
        console.error(`Global ${type}:`, error);
        
        this.systemStatus.errors.push({
            type,
            error: error.message || error,
            timestamp: new Date(),
            stack: error.stack
        });
        
        // Don't show error messages for network errors when offline
        if (!this.systemStatus.online && error.message.includes('fetch')) {
            return;
        }
        
        // Show user-friendly error message
        if (type === 'JavaScript Error') {
            statusManager.showError('An unexpected error occurred. Please refresh the page if problems persist.');
        } else {
            statusManager.showError('A problem occurred while processing your request.');
        }
        
        // Send to error reporting service (if configured)
        this.reportError(error, type, event);
    }

    handleAPIError(errorData) {
        console.error('API Error:', errorData);
        
        if (errorData.status === 401) {
            statusManager.showError('Session expired. Please refresh the page.');
        } else if (errorData.status === 403) {
            statusManager.showError('You do not have permission to perform this action.');
        } else if (errorData.status === 404) {
            statusManager.showError('The requested resource was not found.');
        } else if (errorData.status >= 500) {
            statusManager.showError('Server error. Please try again later.');
        } else {
            statusManager.showError(errorData.message || 'An error occurred while communicating with the server.');
        }
    }

    // Utility Methods
    initializeTheme() {
        // Always use dark-luxury theme
        this.setTheme('dark-luxury', false);
    }

    setTheme(theme, showMessage = false) {
        // Apply dark-luxury theme to body
        document.body.setAttribute('data-theme', 'dark-luxury');
        
        // Save preference
        Utils.storage.set('app_theme', 'dark-luxury');
    }

    async refreshData() {
        statusManager.showInfo('Refreshing data...');
        
        try {
            // Clear API cache
            API.cache.clear();
            
            // Refresh gallery data
            if (this.gallery) {
                await this.gallery.refreshPosts();
            }
            
            Utils.storage.set('last_data_update', Date.now());
            statusManager.showSuccess('Data refreshed successfully');
            
        } catch (error) {
            console.error('Failed to refresh data:', error);
            statusManager.showError('Failed to refresh data: ' + error.message);
        }
    }

    focusSearch() {
        const searchInput = document.getElementById('searchInput');
        if (searchInput) {
            searchInput.focus();
            searchInput.select();
        }
    }

    showHelp() {
        statusManager.showInfo('Help documentation coming soon! Use Ctrl+H to access keyboard shortcuts.');
    }

    showKeyboardShortcuts() {
        const shortcuts = [
            'Ctrl+K: Focus search',
            'Ctrl+R: Refresh data',
            'Ctrl+H: Show help',
            '?: Show this list',
            'Arrow Keys: Navigate pages'
        ];
        
        const message = shortcuts.join('\n');
        statusManager.showInfo(`Keyboard Shortcuts:\n\n${message}`, 10000);
    }

    hasUnsavedChanges() {
        // Check for any active rename operations
        const activeRenames = document.querySelectorAll('.rename-container.active');
        if (activeRenames.length > 0) {
            return true;
        }
        
        return false;
    }

    reportError(error, type, event) {
        // This would typically send to an error reporting service
        // For now, we'll just log it
        console.log('Error Report:', {
            type,
            message: error.message,
            stack: error.stack,
            timestamp: new Date(),
            userAgent: navigator.userAgent,
            url: window.location.href,
            systemStatus: this.systemStatus
        });
    }

    // Public API Methods
    getSystemStatus() {
        return {
            ...this.systemStatus,
            initialized: this.isInitialized,
            galleryLoaded: this.gallery && !this.gallery.isLoading,
            postsCount: this.gallery ? this.gallery.posts.length : 0
        };
    }

    exportData() {
        if (!this.gallery) {
            statusManager.showError('Gallery not loaded');
            return;
        }
        
        const data = {
            posts: this.gallery.posts,
            preferences: {
                gallery: Utils.storage.get('gallery_preferences', {}),
                viewer: Utils.storage.get('viewer_preferences', {}),
                theme: Utils.storage.get('app_theme', 'light')
            },
            exportDate: new Date().toISOString()
        };
        
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `vama-gallery-data-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        statusManager.showSuccess('Data exported successfully');
    }

    initializePlaylists() {
        const playlistsBtn = document.getElementById('playlistsBtn');
        const galleryGrid = document.getElementById('galleryGrid');
        const playlistsGrid = document.getElementById('playlistsGrid');
        const pagination = document.querySelector('.pagination-container');
        
        this.isPlaylistMode = false; // Make it accessible from outside

        // Toggle playlist mode
        playlistsBtn.addEventListener('click', async () => {
            this.isPlaylistMode = !this.isPlaylistMode;
            
            if (this.isPlaylistMode) {
                // Switch to playlist mode
                galleryGrid.style.display = 'none';
                playlistsGrid.style.display = 'grid';
                pagination.style.display = 'none';
                playlistsBtn.classList.add('active');
                await this.loadPlaylistCards();
            } else {
                // Switch back to gallery mode
                galleryGrid.style.display = 'grid';
                playlistsGrid.style.display = 'none';
                pagination.style.display = 'flex';
                playlistsBtn.classList.remove('active');
            }
        });
    }

    async loadPlaylistCards() {
        const playlistsGrid = document.getElementById('playlistsGrid');
        playlistsGrid.innerHTML = '<div style="text-align: center; padding: 2rem; grid-column: 1 / -1;"><div class="spinner"></div><p>Loading playlists...</p></div>';

        try {
            const playlists = await window.playlistManager.fetchPlaylists();
            
            // Always start with create card
            let cardsHtml = this.createPlaylistCreateCard();
            
            // Add playlist cards
            for (const playlist of playlists) {
                cardsHtml += await this.createPlaylistCard(playlist);
            }
            
            playlistsGrid.innerHTML = cardsHtml;
        } catch (error) {
            playlistsGrid.innerHTML = `
                <div style="text-align: center; padding: 2rem; color: var(--danger-color); grid-column: 1 / -1;">
                    <i class="fas fa-exclamation-circle" style="font-size: 3rem; margin-bottom: 1rem;"></i>
                    <p>Failed to load playlists: ${error.message}</p>
                </div>
            `;
        }
    }

    createPlaylistCreateCard() {
        return `
            <div class="gallery-item create-playlist-card" onclick="window.playlistManager.showCreatePlaylistModal()">
                <div class="gallery-image-container">
                    <div class="create-playlist-icon">
                        <i class="fas fa-plus"></i>
                    </div>
                </div>
                <div class="gallery-content">
                    <h3 class="gallery-title">Create Playlist</h3>
                </div>
            </div>
        `;
    }

    async createPlaylistCard(playlist) {
        // Get the first image for thumbnail
        let thumbnailPath;
        if (playlist.images && playlist.images.length > 0) {
            const firstImage = playlist.images[0];
            thumbnailPath = `/extracted/${firstImage.post_id}/${firstImage.filename}`;
        } else {
            thumbnailPath = '/metadata/profile_images/default_playlist_profile.png';
        }

        const imageCount = playlist.images?.length || 0;

        const cardHtml = `
            <div class="gallery-item playlist-card" data-playlist-id="${playlist.playlist_id}" 
                 onclick="window.open('/playlists/${playlist.playlist_id}_cascade.html', '_blank', 'noopener')">
                <div class="gallery-image-container">
                    <img src="${thumbnailPath}" alt="${Utils.sanitizeHtml(playlist.name)}" class="gallery-image" loading="lazy" 
                         onerror="this.src='/metadata/profile_images/default_playlist_profile.png'">
                </div>
                <div class="gallery-content">
                    <div class="gallery-title-row">
                        <h3 class="gallery-title">${Utils.sanitizeHtml(playlist.name)}</h3>
                        <button class="title-rename-btn playlist-edit-btn" type="button" title="Edit playlist"
                                onclick="event.stopPropagation(); app.editPlaylist('${playlist.playlist_id}')">
                            <i class="fas fa-pen"></i>
                        </button>
                    </div>
                    ${playlist.description ? `<p class="playlist-description">${Utils.sanitizeHtml(playlist.description)}</p>` : ''}
                    <div class="gallery-meta">
                        <div class="meta-row meta-row-actions">
                            <span class="meta-label"><i class="fas fa-images"></i> Images: ${imageCount}</span>
                            <div class="meta-actions">
                                <button class="title-rename-btn playlist-delete-btn" type="button" title="Delete playlist"
                                        onclick="event.stopPropagation(); app.deletePlaylist('${playlist.playlist_id}')">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        return cardHtml;
    }

    editPlaylist(playlistId) {
        const playlist = window.playlistManager.playlists.find(p => p.playlist_id === playlistId);
        if (!playlist) {
            statusManager.showError('Playlist not found');
            return;
        }
        window.playlistManager.showEditPlaylistModal(playlist);
    }

    async deletePlaylist(playlistId) {
        const confirmed = await confirmationModal.show(
            'Delete Playlist',
            'Are you sure you want to delete this playlist? This will delete all associated files.',
            'Delete',
            'Cancel'
        );

        if (confirmed) {
            try {
                await window.playlistManager.deletePlaylist(playlistId);
                statusManager.showSuccess('Playlist deleted successfully');
                await this.loadPlaylistCards(); // Reload the cards
            } catch (error) {
                statusManager.showError('Failed to delete playlist: ' + error.message);
            }
        }
    }
}

// Initialize the application when the page loads
let app;

// Check if DOM is already loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        app = new VamaGalleryApp();
    });
} else {
    app = new VamaGalleryApp();
}

// Make app globally available for debugging
window.vamaApp = app;
window.app = app; // Alias for easier access

// Service Worker registration (if available)
if ('serviceWorker' in navigator && window.location.protocol === 'https:') {
    navigator.serviceWorker.register('/sw.js')
        .then(registration => {
            console.log('Service Worker registered:', registration);
        })
        .catch(error => {
            console.log('Service Worker registration failed:', error);
        });
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = VamaGalleryApp;
}