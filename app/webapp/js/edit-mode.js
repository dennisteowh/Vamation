// Edit Mode and Status Management

/**
 * Status Manager for showing notifications and messages
 */
class StatusManager {
    constructor() {
        this.container = null;
        this.activeMessages = new Map();
        this.messageId = 0;
        this.init();
    }

    init() {
        this.container = document.getElementById('statusMessages');
        if (!this.container) {
            this.container = Utils.dom.createElement('div', 'status-messages');
            document.body.appendChild(this.container);
        }
    }

    show(message, type = 'info', duration = 5000) {
        const id = this.messageId++;
        
        const messageElement = Utils.dom.createElement('div', `status-message ${type}`, {
            innerHTML: `
                <span>${Utils.sanitizeHtml(message)}</span>
                <i class="fas fa-times" style="margin-left: auto; cursor: pointer; opacity: 0.7;"></i>
            `
        });

        // Close button
        const closeBtn = messageElement.querySelector('.fa-times');
        closeBtn.addEventListener('click', () => this.hide(id));

        // Auto hide
        if (duration > 0) {
            setTimeout(() => this.hide(id), duration);
        }

        // Add to DOM
        this.container.appendChild(messageElement);
        this.activeMessages.set(id, messageElement);

        // Animate in
        Utils.animation.fadeIn(messageElement, 300);

        return id;
    }

    hide(id) {
        const messageElement = this.activeMessages.get(id);
        if (messageElement) {
            Utils.animation.fadeOut(messageElement, 300);
            setTimeout(() => {
                if (messageElement.parentNode) {
                    messageElement.parentNode.removeChild(messageElement);
                }
                this.activeMessages.delete(id);
            }, 300);
        }
    }

    showSuccess(message, duration = 4000) {
        return this.show(message, 'success', duration);
    }

    showError(message, duration = 8000) {
        return this.show(message, 'error', duration);
    }

    showWarning(message, duration = 6000) {
        return this.show(message, 'warning', duration);
    }

    showInfo(message, duration = 4000) {
        return this.show(message, 'info', duration);
    }

    clear() {
        this.activeMessages.forEach((element, id) => {
            this.hide(id);
        });
    }
}

// Create global instance
const statusManager = new StatusManager();

/**
 * Confirmation Modal for user confirmations
 */
class ConfirmationModal {
    constructor() {
        this.modal = null;
        this.resolver = null;
        this.init();
    }

    init() {
        this.modal = document.getElementById('confirmationModal');
        this.bindEvents();
    }

    bindEvents() {
        const confirmBtn = document.getElementById('confirmAction');
        const cancelBtn = document.getElementById('confirmCancel');

        confirmBtn.addEventListener('click', () => this.confirm(true));
        cancelBtn.addEventListener('click', () => this.confirm(false));

        // Close on backdrop click
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal) {
                this.confirm(false);
            }
        });

        // Close on escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal.classList.contains('active')) {
                this.confirm(false);
            }
        });
    }

    show(title, message, confirmText = 'Confirm', cancelText = 'Cancel') {
        return new Promise((resolve) => {
            this.resolver = resolve;

            // Set content
            this.modal.querySelector('.modal-header h3').textContent = title;
            document.getElementById('confirmationMessage').textContent = message;
            document.getElementById('confirmAction').textContent = confirmText;
            document.getElementById('confirmCancel').textContent = cancelText;

            // Show modal
            this.modal.style.display = 'flex';
            this.modal.classList.add('active');
            
            // Prevent body scrolling
            document.body.style.overflow = 'hidden';
        });
    }

    confirm(result) {
        if (this.resolver) {
            this.resolver(result);
            this.resolver = null;
        }

        this.modal.style.display = 'none';
        this.modal.classList.remove('active');
        
        // Restore body scrolling if no other modals are open
        const openModals = document.querySelectorAll('.modal.active');
        if (openModals.length === 0) {
            document.body.style.overflow = '';
        }
    }
}

// Create global instance
const confirmationModal = new ConfirmationModal();

/**
 * Edit Mode Manager - Handles batch operations and advanced editing
 */
class EditModeManager {
    constructor() {
        this.isActive = false;
        this.selectedPosts = new Set();
        this.batchBar = null;
        this.gallery = null;
        
        this.init();
    }

    init() {
        this.createBatchBar();
        this.bindEvents();
    }

    setGallery(gallery) {
        this.gallery = gallery;
    }

    createBatchBar() {
        this.batchBar = Utils.dom.createElement('div', 'batch-edit-bar', {
            innerHTML: `
                <div class="batch-edit-info">
                    <i class="fas fa-check-circle"></i>
                    <span id="batchEditCount">0 posts selected</span>
                </div>
                <div class="batch-edit-actions">
                    <button class="batch-edit-btn" data-action="show">
                        <i class="fas fa-eye"></i> Show All
                    </button>
                    <button class="batch-edit-btn" data-action="hide">
                        <i class="fas fa-eye-slash"></i> Hide All
                    </button>
                    <button class="batch-edit-btn" data-action="download">
                        <i class="fas fa-folder-plus"></i> Download All
                    </button>
                    <button class="batch-edit-btn" data-action="delete">
                        <i class="fas fa-trash"></i> Delete All
                    </button>
                    <button class="batch-edit-btn" data-action="clear">
                        <i class="fas fa-times"></i> Clear Selection
                    </button>
                </div>
            `
        });

        document.body.appendChild(this.batchBar);
    }

    bindEvents() {
        // Batch action buttons
        this.batchBar.addEventListener('click', (e) => {
            const button = e.target.closest('[data-action]');
            if (button) {
                const action = button.dataset.action;
                this.handleBatchAction(action);
            }
        });

        // Listen for selection changes
        document.addEventListener('postSelectionChanged', (e) => {
            this.updateSelectionDisplay();
        });
    }

    activate() {
        this.isActive = true;
        this.selectedPosts.clear();
        this.updateBatchBar();
        
        // Add selection listeners to gallery items
        this.addSelectionListeners();
    }

    deactivate() {
        this.isActive = false;
        this.selectedPosts.clear();
        this.updateBatchBar();
        
        // Remove selection listeners
        this.removeSelectionListeners();
        
        // Clear selected state from items
        document.querySelectorAll('.gallery-item.selected').forEach(item => {
            item.classList.remove('selected');
        });
    }

    addSelectionListeners() {
        const galleryItems = document.querySelectorAll('.gallery-item');
        galleryItems.forEach(item => {
            item.addEventListener('click', this.handleItemSelection.bind(this), true);
        });
    }

    removeSelectionListeners() {
        const galleryItems = document.querySelectorAll('.gallery-item');
        galleryItems.forEach(item => {
            item.removeEventListener('click', this.handleItemSelection.bind(this), true);
        });
    }

    handleItemSelection(e) {
        if (!this.isActive) return;
        
        e.stopPropagation();
        e.preventDefault();
        
        const item = e.target.closest('.gallery-item');
        if (!item) return;
        
        const postId = item.dataset.postId;
        if (!postId) return;
        
        // Toggle selection
        if (this.selectedPosts.has(postId)) {
            this.selectedPosts.delete(postId);
            item.classList.remove('selected');
        } else {
            this.selectedPosts.add(postId);
            item.classList.add('selected');
        }
        
        this.updateSelectionDisplay();
        
        // Dispatch custom event
        document.dispatchEvent(new CustomEvent('postSelectionChanged', {
            detail: { selectedPosts: Array.from(this.selectedPosts) }
        }));
    }

    updateSelectionDisplay() {
        const count = this.selectedPosts.size;
        const countElement = document.getElementById('batchEditCount');
        
        if (countElement) {
            countElement.textContent = count === 0 
                ? 'No posts selected'
                : `${count} post${count === 1 ? '' : 's'} selected`;
        }
        
        this.updateBatchBar();
    }

    updateBatchBar() {
        const hasSelection = this.selectedPosts.size > 0;
        
        if (this.isActive && hasSelection) {
            this.batchBar.classList.add('active');
        } else {
            this.batchBar.classList.remove('active');
        }
    }

    async handleBatchAction(action) {
        if (this.selectedPosts.size === 0) {
            statusManager.showWarning('No posts selected');
            return;
        }

        const selectedPostIds = Array.from(this.selectedPosts);
        const count = selectedPostIds.length;

        switch (action) {
            case 'show':
                await this.batchToggleVisibility(selectedPostIds, true);
                break;
                
            case 'hide':
                await this.batchToggleVisibility(selectedPostIds, false);
                break;
                
            case 'download':
                await this.batchDownload(selectedPostIds);
                break;
                
            case 'delete':
                await this.batchDelete(selectedPostIds);
                break;
                
            case 'clear':
                this.clearSelection();
                break;
        }
    }

    async batchToggleVisibility(postIds, show) {
        const action = show ? 'show' : 'hide';
        const confirmed = await confirmationModal.show(
            `${action.charAt(0).toUpperCase() + action.slice(1)} Posts`,
            `Are you sure you want to ${action} ${postIds.length} post(s)?`
        );

        if (!confirmed) return;

        statusManager.showInfo(`${action.charAt(0).toUpperCase() + action.slice(1)}ing ${postIds.length} posts...`);

        let successCount = 0;
        let errorCount = 0;

        for (const postId of postIds) {
            try {
                const result = await API.metadata.updatePost(postId, { display: show });
                if (result.success) {
                    successCount++;
                    
                    // Update gallery item
                    if (this.gallery) {
                        const post = this.gallery.posts.find(p => p.post_id === postId);
                        if (post) {
                            post.display = show;
                            this.gallery.updatePostInGrid(post);
                        }
                    }
                } else {
                    errorCount++;
                }
            } catch (error) {
                console.error(`Failed to ${action} post ${postId}:`, error);
                errorCount++;
            }
        }

        if (successCount > 0) {
            statusManager.showSuccess(`${successCount} post(s) ${action}n successfully`);
        }
        if (errorCount > 0) {
            statusManager.showError(`Failed to ${action} ${errorCount} post(s)`);
        }

        this.clearSelection();
    }

    async batchDownload(postIds) {
        const confirmed = await confirmationModal.show(
            'Download Files',
            `Are you sure you want to download files for ${postIds.length} post(s)? This may take some time.`
        );

        if (!confirmed) return;

        statusManager.showInfo(`Queueing downloads for ${postIds.length} posts...`);

        let successCount = 0;
        let errorCount = 0;

        for (const postId of postIds) {
            try {
                const result = await API.files.download(postId);
                if (result.success) {
                    successCount++;

                    if (this.gallery) {
                        const post = this.gallery.posts.find(p => p.post_id === postId);
                        if (post) {
                            this.gallery.setExtractionLoading(post, true, 'queued');
                            this.gallery.updateExtractionProgress(postId, {
                                step: 'queued',
                                message: 'Queued for download...',
                                percent: 0
                            }, 'queued');
                        }
                    }
                } else {
                    errorCount++;
                }
            } catch (error) {
                console.error(`Failed to queue download for post ${postId}:`, error);
                errorCount++;
            }
            
            // Add small delay to prevent overwhelming the server
            await new Promise(resolve => setTimeout(resolve, 200));
        }

        if (successCount > 0) {
            statusManager.showSuccess(`${successCount} post(s) queued for download`);
        }
        if (errorCount > 0) {
            statusManager.showError(`Failed to queue download for ${errorCount} post(s)`);
        }

        this.clearSelection();
    }

    async batchDelete(postIds) {
        const confirmed = await confirmationModal.show(
            'Delete Posts',
            `Are you sure you want to permanently delete ALL FILES for ${postIds.length} post(s)? This action cannot be undone!`,
            'Delete Permanently',
            'Cancel'
        );

        if (!confirmed) return;

        statusManager.showInfo(`Deleting ${postIds.length} posts...`);

        let successCount = 0;
        let errorCount = 0;

        for (const postId of postIds) {
            try {
                const result = await API.files.deleteAll(postId);
                if (result.success) {
                    successCount++;
                    
                    // Remove from gallery
                    if (this.gallery) {
                        this.gallery.posts = this.gallery.posts.filter(p => p.post_id !== postId);
                    }
                } else {
                    errorCount++;
                }
            } catch (error) {
                console.error(`Failed to delete post ${postId}:`, error);
                errorCount++;
            }
            
            // Add small delay
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        if (successCount > 0) {
            statusManager.showSuccess(`${successCount} post(s) deleted successfully`);
            
            // Refresh gallery
            if (this.gallery) {
                this.gallery.filterAndSort();
                this.gallery.render();
            }
        }
        if (errorCount > 0) {
            statusManager.showError(`Failed to delete ${errorCount} post(s)`);
        }

        this.clearSelection();
    }

    clearSelection() {
        this.selectedPosts.clear();
        document.querySelectorAll('.gallery-item.selected').forEach(item => {
            item.classList.remove('selected');
        });
        this.updateSelectionDisplay();
    }

    selectAll() {
        if (!this.gallery) return;
        
        const visibleItems = document.querySelectorAll('.gallery-item');
        visibleItems.forEach(item => {
            const postId = item.dataset.postId;
            if (postId) {
                this.selectedPosts.add(postId);
                item.classList.add('selected');
            }
        });
        
        this.updateSelectionDisplay();
    }

    selectNone() {
        this.clearSelection();
    }

    invertSelection() {
        if (!this.gallery) return;
        
        const visibleItems = document.querySelectorAll('.gallery-item');
        const newSelection = new Set();
        
        visibleItems.forEach(item => {
            const postId = item.dataset.postId;
            if (postId) {
                if (this.selectedPosts.has(postId)) {
                    item.classList.remove('selected');
                } else {
                    newSelection.add(postId);
                    item.classList.add('selected');
                }
            }
        });
        
        this.selectedPosts = newSelection;
        this.updateSelectionDisplay();
    }
}

// Create global instance
const editModeManager = new EditModeManager();

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { statusManager, confirmationModal, editModeManager };
}