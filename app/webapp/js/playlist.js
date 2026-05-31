/**
 * Playlist Management Module
 * Handles playlist CRUD operations and UI interactions
 */

class PlaylistManager {
    constructor(app = null) {
        this.app = app;
        this.playlists = [];
        this.currentPlaylist = null;
    }

    /**
     * Fetch all playlists from the server
     */
    async fetchPlaylists() {
        try {
            const response = await fetch('/api/playlists');
            if (!response.ok) {
                throw new Error(`Failed to fetch playlists: ${response.statusText}`);
            }
            const data = await response.json();
            this.playlists = data.playlists || [];
            return this.playlists;
        } catch (error) {
            console.error('Error fetching playlists:', error);
            throw error;
        }
    }

    /**
     * Create a new playlist
     */
    async createPlaylist(name, description = '') {
        try {
            const response = await fetch('/api/playlists', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ name, description })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to create playlist');
            }

            const data = await response.json();
            await this.fetchPlaylists(); // Refresh list
            return data.playlist;
        } catch (error) {
            console.error('Error creating playlist:', error);
            throw error;
        }
    }

    /**
     * Update playlist metadata (name, description)
     */
    async updatePlaylist(playlistId, updates) {
        try {
            const response = await fetch(`/api/playlists/${playlistId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(updates)
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to update playlist');
            }

            const data = await response.json();
            await this.fetchPlaylists(); // Refresh list
            return data.playlist;
        } catch (error) {
            console.error('Error updating playlist:', error);
            throw error;
        }
    }

    /**
     * Delete a playlist
     */
    async deletePlaylist(playlistId) {
        try {
            const response = await fetch(`/api/playlists/${playlistId}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to delete playlist');
            }

            await this.fetchPlaylists(); // Refresh list
            return true;
        } catch (error) {
            console.error('Error deleting playlist:', error);
            throw error;
        }
    }

    /**
     * Add images to a playlist
     */
    async addImagesToPlaylist(playlistId, images) {
        try {
            const response = await fetch(`/api/playlists/${playlistId}/images`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ images })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to add images');
            }

            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Error adding images to playlist:', error);
            throw error;
        }
    }

    /**
     * Remove images from a playlist
     */
    async removeImagesFromPlaylist(playlistId, images) {
        try {
            const response = await fetch(`/api/playlists/${playlistId}/images`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ images })
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'Failed to remove images');
            }

            return await response.json();
        } catch (error) {
            console.error('Error removing images from playlist:', error);
            throw error;
        }
    }

    /**
     * Get a specific playlist
     */
    async getPlaylist(playlistId) {
        try {
            const response = await fetch(`/api/playlists/${playlistId}`);
            if (!response.ok) {
                throw new Error(`Failed to fetch playlist: ${response.statusText}`);
            }
            const data = await response.json();
            return data.playlist;
        } catch (error) {
            console.error('Error fetching playlist:', error);
            throw error;
        }
    }

    /**
     * Show create playlist modal
     */
    showCreatePlaylistModal() {
        const modal = document.getElementById('createPlaylistModal');
        const nameInput = document.getElementById('playlistName');
        const descriptionInput = document.getElementById('playlistDescription');
        const createBtn = document.getElementById('confirmCreatePlaylistBtn');
        const cancelBtn = document.getElementById('cancelPlaylistBtn');
        const closeBtn = modal.querySelector('.close-modal');

        // Clear previous values
        nameInput.value = '';
        descriptionInput.value = '';
        createBtn.disabled = false;
        createBtn.innerHTML = 'Create';

        // Show modal
        modal.classList.add('active');

        const closeModal = () => {
            modal.classList.remove('active');
            // Remove event listeners
            createBtn.replaceWith(createBtn.cloneNode(true));
            cancelBtn.replaceWith(cancelBtn.cloneNode(true));
        };

        // Event listeners (get fresh references after potential cloning)
        const newCreateBtn = document.getElementById('confirmCreatePlaylistBtn');
        const newCancelBtn = document.getElementById('cancelPlaylistBtn');

        const handleCreate = async () => {
            const name = nameInput.value.trim();
            const description = descriptionInput.value.trim();

            if (!name) {
                statusManager.showError('Please enter a playlist name');
                nameInput.focus();
                return;
            }

            try {
                newCreateBtn.disabled = true;
                newCreateBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating...';
                
                await this.createPlaylist(name, description);
                
                closeModal();
                
                // Trigger refresh of playlists view if active
                const playlistsGrid = document.getElementById('playlistsGrid');
                console.log('playlistsGrid:', playlistsGrid);
                console.log('playlistsGrid.style.display:', playlistsGrid?.style.display);
                console.log('this.app exists:', !!this.app);
                console.log('loadPlaylistCards exists:', typeof this.app?.loadPlaylistCards);
                
                if (this.app && playlistsGrid && playlistsGrid.style.display !== 'none') {
                    console.log('Calling loadPlaylistCards...');
                    await this.app.loadPlaylistCards();
                    console.log('loadPlaylistCards completed');
                }
                
                statusManager.showSuccess('Playlist created successfully');
            } catch (error) {
                statusManager.showError(`Failed to create playlist: ${error.message}`);
                newCreateBtn.disabled = false;
                newCreateBtn.innerHTML = 'Create';
            }
        };

        newCreateBtn.addEventListener('click', handleCreate);
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
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleCreate();
            }
        };
        nameInput.addEventListener('keydown', enterHandler);

        nameInput.focus();
    }

    /**
     * Show edit playlist modal
     */
    showEditPlaylistModal(playlist) {
        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-content playlist-modal">
                <div class="modal-header">
                    <h2>Edit Playlist</h2>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label for="edit-playlist-name">Playlist Name *</label>
                        <input type="text" id="edit-playlist-name" class="form-input" value="${playlist.name}" required>
                    </div>
                    <div class="form-group">
                        <label for="edit-playlist-description">Description</label>
                        <textarea id="edit-playlist-description" class="form-input" rows="3">${playlist.description || ''}</textarea>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary modal-cancel">Cancel</button>
                    <button class="btn btn-primary modal-save">Save</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Event listeners
        const closeBtn = modal.querySelector('.modal-close');
        const cancelBtn = modal.querySelector('.modal-cancel');
        const saveBtn = modal.querySelector('.modal-save');
        const nameInput = modal.querySelector('#edit-playlist-name');

        const closeModal = () => {
            modal.remove();
        };

        closeBtn.addEventListener('click', closeModal);
        cancelBtn.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });

        saveBtn.addEventListener('click', async () => {
            const name = nameInput.value.trim();
            const description = modal.querySelector('#edit-playlist-description').value.trim();

            if (!name) {
                alert('Please enter a playlist name');
                return;
            }

            try {
                saveBtn.disabled = true;
                saveBtn.textContent = 'Saving...';
                
                await this.updatePlaylist(playlist.playlist_id, { name, description });
                closeModal();
                
                // Trigger refresh of playlists view
                if (window.gallery && window.gallery.currentView === 'playlists') {
                    window.gallery.showPlaylistsView();
                }
            } catch (error) {
                alert(`Failed to update playlist: ${error.message}`);
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
        });

        nameInput.focus();
    }

    /**
     * Show delete confirmation modal
     */
    showDeletePlaylistModal(playlist) {
        const modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-content playlist-modal">
                <div class="modal-header">
                    <h2>Delete Playlist</h2>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <p>Are you sure you want to delete the playlist "<strong>${playlist.name}</strong>"?</p>
                    <p class="text-muted">This action cannot be undone. The images will not be deleted.</p>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary modal-cancel">Cancel</button>
                    <button class="btn btn-danger modal-delete">Delete</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Event listeners
        const closeBtn = modal.querySelector('.modal-close');
        const cancelBtn = modal.querySelector('.modal-cancel');
        const deleteBtn = modal.querySelector('.modal-delete');

        const closeModal = () => {
            modal.remove();
        };

        closeBtn.addEventListener('click', closeModal);
        cancelBtn.addEventListener('click', closeModal);
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });

        deleteBtn.addEventListener('click', async () => {
            try {
                deleteBtn.disabled = true;
                deleteBtn.textContent = 'Deleting...';
                
                await this.deletePlaylist(playlist.playlist_id);
                closeModal();
                
                // Trigger refresh of playlists view
                if (window.gallery && window.gallery.currentView === 'playlists') {
                    window.gallery.showPlaylistsView();
                }
            } catch (error) {
                alert(`Failed to delete playlist: ${error.message}`);
                deleteBtn.disabled = false;
                deleteBtn.textContent = 'Delete';
            }
        });
    }

    /**
     * Show add to playlist dropdown
     */
    showAddToPlaylistDropdown(postId, filename, anchorElement) {
        // Remove any existing dropdown
        const existingDropdown = document.querySelector('.add-to-playlist-dropdown');
        if (existingDropdown) {
            existingDropdown.remove();
        }

        const dropdown = document.createElement('div');
        dropdown.className = 'add-to-playlist-dropdown';
        
        // Position dropdown below anchor
        const rect = anchorElement.getBoundingClientRect();
        dropdown.style.top = `${rect.bottom + 5}px`;
        dropdown.style.left = `${rect.left}px`;

        // Build dropdown content
        if (this.playlists.length === 0) {
            dropdown.innerHTML = `
                <div class="dropdown-item dropdown-empty">
                    No playlists yet
                </div>
                <div class="dropdown-divider"></div>
                <div class="dropdown-item dropdown-create" data-action="create">
                    <span class="icon">+</span> Create New Playlist
                </div>
            `;
        } else {
            const playlistItems = this.playlists.map(p => `
                <div class="dropdown-item" data-playlist-id="${p.playlist_id}">
                    <span class="icon">📁</span> ${p.name}
                </div>
            `).join('');

            dropdown.innerHTML = `
                ${playlistItems}
                <div class="dropdown-divider"></div>
                <div class="dropdown-item dropdown-create" data-action="create">
                    <span class="icon">+</span> Create New Playlist
                </div>
            `;
        }

        document.body.appendChild(dropdown);

        // Event listeners
        dropdown.addEventListener('click', async (e) => {
            const item = e.target.closest('.dropdown-item');
            if (!item) return;

            const playlistId = item.dataset.playlistId;
            const action = item.dataset.action;

            if (action === 'create') {
                dropdown.remove();
                this.showCreatePlaylistModal();
            } else if (playlistId) {
                try {
                    await this.addImagesToPlaylist(playlistId, [{ post_id: postId, filename }]);
                    dropdown.remove();
                    
                    // Show success notification
                    this.showNotification('Image added to playlist', 'success');
                } catch (error) {
                    alert(`Failed to add image: ${error.message}`);
                }
            }
        });

        // Close on outside click
        setTimeout(() => {
            document.addEventListener('click', function closeDropdown(e) {
                if (!dropdown.contains(e.target) && e.target !== anchorElement) {
                    dropdown.remove();
                    document.removeEventListener('click', closeDropdown);
                }
            });
        }, 0);
    }

    /**
     * Show notification toast
     */
    showNotification(message, type = 'info') {
        // Use the global statusManager for consistent notifications
        switch(type) {
            case 'success':
                statusManager.showSuccess(message);
                break;
            case 'error':
                statusManager.showError(message);
                break;
            case 'warning':
                statusManager.showWarning(message);
                break;
            default:
                statusManager.showInfo(message);
        }
    }
}

// Initialize global playlist manager
// PlaylistManager will be instantiated by main app with app reference
