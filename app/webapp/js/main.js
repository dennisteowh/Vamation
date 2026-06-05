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
            
            const isEditModeActive = document.body.classList.contains('edit-mode-active');
            console.log('loadPlaylistCards - edit-mode-active on body?', isEditModeActive);
            
            // Always start with create card
            let cardsHtml = this.createPlaylistCreateCard();
            
            // Add playlist cards
            for (const playlist of playlists) {
                cardsHtml += await this.createPlaylistCard(playlist);
            }
            
            playlistsGrid.innerHTML = cardsHtml;
            console.log('loadPlaylistCards - Cards HTML set, total playlists:', playlists.length);
            
            // Verify DOM after setting innerHTML
            const cardsInDom = playlistsGrid.querySelectorAll('.playlist-card');
            const cardsWithEditMode = playlistsGrid.querySelectorAll('.playlist-card.edit-mode');
            const overlaysInDom = playlistsGrid.querySelectorAll('.edit-overlay');
            
            console.log('loadPlaylistCards - Cards in DOM:', cardsInDom.length);
            console.log('loadPlaylistCards - Cards with edit-mode class:', cardsWithEditMode.length);
            console.log('loadPlaylistCards - Edit overlays in DOM:', overlaysInDom.length);
            
            if (isEditModeActive && overlaysInDom.length === 0) {
                console.error('❌ ERROR: Edit mode is active but no overlays found in DOM!');
            }
            
            if (isEditModeActive && cardsWithEditMode.length === 0) {
                console.error('❌ ERROR: Edit mode is active but no cards have edit-mode class!');
            }
            
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
        const isEditMode = document.body.classList.contains('edit-mode-active');
        
        return `
            <div class="gallery-item create-playlist-card ${isEditMode ? 'edit-mode' : ''}" onclick="window.playlistManager.showCreatePlaylistModal()">
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

        const isEditMode = document.body.classList.contains('edit-mode-active');
        const imageCount = playlist.images?.length || 0;
        
        console.log('createPlaylistCard - Playlist:', playlist.name);
        console.log('createPlaylistCard - isEditMode:', isEditMode);
        console.log('createPlaylistCard - Will include edit-overlay:', isEditMode);

        const cardHtml = `
            <div class="gallery-item playlist-card ${isEditMode ? 'edit-mode' : ''}" data-playlist-id="${playlist.playlist_id}" 
                 onclick="${!isEditMode ? `window.location.href='/playlists/${playlist.playlist_id}_cascade.html'` : ''}">
                <div class="gallery-image-container">
                    <img src="${thumbnailPath}" alt="${Utils.sanitizeHtml(playlist.name)}" class="gallery-image" loading="lazy" 
                         onerror="this.src='/metadata/profile_images/default_playlist_profile.png'">
                    ${isEditMode ? `
                    <div class="edit-overlay">
                        <div class="edit-controls">
                            <div class="edit-controls-header">
                                <h4 class="edit-controls-title">Playlist Options</h4>
                            </div>
                            <button class="edit-btn rename" onclick="app.renamePlaylist('${playlist.playlist_id}', '${Utils.sanitizeHtml(playlist.name).replace(/'/g, "\\'")}')" title="Rename Playlist">
                                <i class="fas fa-edit"></i>
                                <span class="btn-text">Rename</span>
                            </button>
                            <button class="edit-btn delete" onclick="app.deletePlaylist('${playlist.playlist_id}')" title="Delete Playlist">
                                <i class="fas fa-trash"></i>
                                <span class="btn-text">Delete</span>
                            </button>
                        </div>
                    </div>
                    ` : ''}
                </div>
                <div class="gallery-content">
                    <h3 class="gallery-title">${Utils.sanitizeHtml(playlist.name)}</h3>
                    ${playlist.description ? `<p class="playlist-description">${Utils.sanitizeHtml(playlist.description)}</p>` : ''}
                    <div class="gallery-meta">
                        <div class="meta-row">
                            <span class="meta-label"><i class="fas fa-images"></i> Images: ${imageCount}</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        console.log('createPlaylistCard - HTML includes edit-overlay:', cardHtml.includes('edit-overlay'));
        console.log('createPlaylistCard - HTML includes edit-mode class:', cardHtml.includes('edit-mode'));
        
        return cardHtml;
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

    async renamePlaylist(playlistId, currentName) {
        const modal = document.getElementById('renamePlaylistModal');
        const nameInput = document.getElementById('renamePlaylistName');
        const renameBtn = document.getElementById('confirmRenameBtn');
        const cancelBtn = document.getElementById('cancelRenameBtn');
        const closeBtn = modal.querySelector('.close-modal');

        // Set current name
        nameInput.value = currentName;
        renameBtn.disabled = false;
        renameBtn.innerHTML = 'Rename';

        // Show modal
        modal.classList.add('active');

        const closeModal = () => {
            modal.classList.remove('active');
            // Remove event listeners by cloning
            renameBtn.replaceWith(renameBtn.cloneNode(true));
            cancelBtn.replaceWith(cancelBtn.cloneNode(true));
        };

        // Get fresh references after potential cloning
        const newRenameBtn = document.getElementById('confirmRenameBtn');
        const newCancelBtn = document.getElementById('cancelRenameBtn');

        const handleRename = async () => {
            const newName = nameInput.value.trim();
            
            if (!newName) {
                statusManager.showError('Please enter a playlist name');
                nameInput.focus();
                return;
            }

            if (newName === currentName) {
                closeModal();
                return;
            }

            try {
                newRenameBtn.disabled = true;
                newRenameBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Renaming...';
                
                await window.playlistManager.updatePlaylist(playlistId, { name: newName });
                
                statusManager.showSuccess('Playlist renamed successfully');
                closeModal();
                await this.loadPlaylistCards(); // Reload the cards
            } catch (error) {
                statusManager.showError('Failed to rename playlist: ' + error.message);
                newRenameBtn.disabled = false;
                newRenameBtn.innerHTML = 'Rename';
            }
        };

        newRenameBtn.addEventListener('click', handleRename);
        newCancelBtn.addEventListener('click', closeModal);
        closeBtn.addEventListener('click', closeModal);
        
        // Close on backdrop click
        const backdropHandler = (e) => {
            if (e.target === modal) {
                closeModal();
                modal.removeEventListener('click', backdropHandler);
            }
        };
        modal.addEventListener('click', backdropHandler);

        // Handle Enter key
        const enterHandler = (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                handleRename();
            }
        };
        nameInput.addEventListener('keydown', enterHandler);

        nameInput.focus();
        nameInput.select();
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