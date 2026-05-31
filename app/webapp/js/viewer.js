// Image Viewer - Single image view and cascade view functionality

/**
 * Viewer class handles single image viewing and cascade view
 */
class Viewer {
    constructor() {
        this.currentPost = null;
        this.currentImages = [];
        this.currentImageIndex = 0;
        this.cascadeImages = [];
        this.cascadeMetadata = {};
        this.cascadePage = 1;
        this.cascadeItemsPerPage = 100;
        this.cascadeImageSize = 200;
        this.cascadeEditMode = false;
        this.isLoading = false;
        
        // DOM elements
        this.singleImageModal = null;
        this.cascadeModal = null;
        this.singleImage = null;
        this.imageCounter = null;
        this.prevBtn = null;
        this.nextBtn = null;
        this.cascadeGrid = null;
        this.imageSizeSlider = null;
        this.cascadeEditModeBtn = null;
        this.cascadePagination = null;
        this.cascadePaginationInfo = null;
        this.cascadeItemsPerPageSelect = null;
        
        // Preload cache
        this.preloadCache = new Map();
        this.preloadQueue = [];
        
        this.init();
    }

    init() {
        this.bindElements();
        this.bindEvents();
        this.loadPreferences();
    }

    bindElements() {
        this.singleImageModal = document.getElementById('singleImageModal');
        this.cascadeModal = document.getElementById('cascadeModal');
        this.singleImage = document.getElementById('singleImage');
        this.imageCounter = document.getElementById('imageCounter');
        this.prevBtn = document.getElementById('prevImage');
        this.nextBtn = document.getElementById('nextImage');
        this.cascadeGrid = document.getElementById('cascadeGrid');
        this.imageSizeSlider = document.getElementById('imageSizeSlider');
        this.cascadeEditModeBtn = document.getElementById('cascadeEditModeBtn');
        this.cascadePagination = document.getElementById('cascadePagination');
        this.cascadePaginationInfo = document.getElementById('cascadePaginationInfo');
        this.cascadeItemsPerPageSelect = document.getElementById('cascadeItemsPerPage');
    }

    bindEvents() {
        // Single image modal
        this.prevBtn.addEventListener('click', () => this.previousImage());
        this.nextBtn.addEventListener('click', () => this.nextImage());
        
        document.getElementById('switchToCascade').addEventListener('click', () => this.switchToCascade());
        document.getElementById('switchToSingle').addEventListener('click', () => this.switchToSingle());
        
        // Cascade controls
        this.imageSizeSlider.addEventListener('input', (e) => this.setCascadeImageSize(parseInt(e.target.value)));
        this.cascadeEditModeBtn.addEventListener('click', () => this.toggleCascadeEditMode());
        this.cascadeItemsPerPageSelect.addEventListener('change', (e) => {
            this.cascadeItemsPerPage = parseInt(e.target.value);
            this.renderCascade();
        });
        
        // Modal close events
        document.querySelectorAll('.close-modal').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const modalId = e.target.dataset.modal || e.target.closest('button').dataset.modal;
                this.closeModal(modalId);
            });
        });
        
        // Keyboard navigation
        document.addEventListener('keydown', (e) => this.handleKeyboard(e));
        
        // Modal backdrop clicks
        [this.singleImageModal, this.cascadeModal].forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    this.closeModal(modal.id);
                }
            });
        });
        
        // Image loading events
        this.singleImage.addEventListener('load', () => this.onImageLoad());
        this.singleImage.addEventListener('error', () => this.onImageError());
    }

    loadPreferences() {
        const prefs = Utils.storage.get('viewer_preferences', {});
        
        if (prefs.cascadeImageSize) {
            this.cascadeImageSize = prefs.cascadeImageSize;
            this.imageSizeSlider.value = this.cascadeImageSize;
            this.updateCascadeImageSize();
        }
        
        if (prefs.cascadeItemsPerPage) {
            this.cascadeItemsPerPage = prefs.cascadeItemsPerPage;
            this.cascadeItemsPerPageSelect.value = this.cascadeItemsPerPage;
        }
    }

    savePreferences() {
        Utils.storage.set('viewer_preferences', {
            cascadeImageSize: this.cascadeImageSize,
            cascadeItemsPerPage: this.cascadeItemsPerPage
        });
    }

    async openPost(post) {
        this.currentPost = post;
        
        try {
            // Load images for the post
            await this.loadPostImages(post);
            
            if (this.currentImages.length === 0) {
                statusManager.showError('No images found for this post');
                return;
            }
            
            // Start with single image view
            this.currentImageIndex = 0;
            this.showSingleImageModal();
            this.displayCurrentImage();
            
        } catch (error) {
            console.error('Failed to open post:', error);
            statusManager.showError('Failed to load post images: ' + error.message);
        }
    }

    async loadPostImages(post) {
        this.setLoading(true);
        
        try {
            // Load with hidden images if in edit mode
            const includeHidden = this.cascadeEditMode;
            const result = await API.cascade.getImages(post.post_id, includeHidden);
            
            if (result.success) {
                this.currentImages = result.data.images || [];
                this.cascadeImages = [...this.currentImages];
                this.cascadeMetadata = result.data.cascade_metadata || {};
                
                // Start preloading images
                this.startPreloading();
                
            } else {
                throw new Error(result.error || 'Failed to load images');
            }
        } catch (error) {
            console.error('Failed to load post images:', error);
            this.currentImages = [];
            this.cascadeImages = [];
            this.cascadeMetadata = {};
            throw error;
        } finally {
            this.setLoading(false);
        }
    }

    showSingleImageModal() {
        this.singleImageModal.style.display = 'flex';
        this.singleImageModal.classList.add('active');
        
        // Set modal title
        document.getElementById('singleImageTitle').textContent = this.currentPost.revised_post_name;
        
        // Prevent body scrolling
        document.body.style.overflow = 'hidden';
    }

    showCascadeModal() {
        this.cascadeModal.style.display = 'flex';
        this.cascadeModal.classList.add('active');
        
        // Set modal title
        document.getElementById('cascadeTitle').textContent = this.currentPost.revised_post_name + ' - Cascade View';
        
        // Render cascade
        this.renderCascade();
        
        // Prevent body scrolling
        document.body.style.overflow = 'hidden';
    }

    closeModal(modalId) {
        const modal = document.getElementById(modalId);
        if (modal) {
            modal.style.display = 'none';
            modal.classList.remove('active');
        }
        
        // Restore body scrolling if no modals are open
        const openModals = document.querySelectorAll('.modal.active');
        if (openModals.length === 0) {
            document.body.style.overflow = '';
            this.cleanup();
        }
    }

    cleanup() {
        // Stop preloading
        this.preloadQueue = [];
        
        // Clear current state
        this.currentPost = null;
        this.currentImages = [];
        this.cascadeImages = [];
        this.currentImageIndex = 0;
        
        // Exit edit mode
        if (this.cascadeEditMode) {
            this.toggleCascadeEditMode();
        }
    }

    displayCurrentImage() {
        if (this.currentImages.length === 0) return;
        
        const image = this.currentImages[this.currentImageIndex];
        if (!image) return;
        
        // Update image source
        const imageUrl = API.images.getContentImageUrl(this.currentPost.post_id, image.filename);
        this.singleImage.src = imageUrl;
        
        // Update counter
        this.imageCounter.textContent = `${this.currentImageIndex + 1} / ${this.currentImages.length}`;
        
        // Update navigation buttons
        this.prevBtn.disabled = this.currentImageIndex <= 0;
        this.nextBtn.disabled = this.currentImageIndex >= this.currentImages.length - 1;
        
        // Preload adjacent images
        this.preloadAdjacentImages();
    }

    previousImage() {
        if (this.currentImageIndex > 0) {
            this.currentImageIndex--;
            this.displayCurrentImage();
        }
    }

    nextImage() {
        if (this.currentImageIndex < this.currentImages.length - 1) {
            this.currentImageIndex++;
            this.displayCurrentImage();
        }
    }

    switchToCascade() {
        this.closeModal('singleImageModal');
        this.showCascadeModal();
    }

    switchToSingle() {
        this.closeModal('cascadeModal');
        this.showSingleImageModal();
        this.displayCurrentImage();
    }

    renderCascade() {
        if (!this.cascadeGrid) return;
        
        this.cascadeGrid.innerHTML = '';
        
        if (this.cascadeImages.length === 0) {
            this.renderCascadeEmptyState();
            return;
        }
        
        // Calculate pagination
        const totalPages = Math.ceil(this.cascadeImages.length / this.cascadeItemsPerPage);
        this.cascadePage = Math.min(this.cascadePage, totalPages);
        if (this.cascadePage < 1) this.cascadePage = 1;
        
        const startIndex = (this.cascadePage - 1) * this.cascadeItemsPerPage;
        const endIndex = startIndex + this.cascadeItemsPerPage;
        const imagesToShow = this.cascadeImages.slice(startIndex, endIndex);
        
        // Update grid template
        this.updateCascadeImageSize();
        
        // Create cascade items
        imagesToShow.forEach((image, index) => {
            const globalIndex = startIndex + index;
            const item = this.createCascadeItem(image, globalIndex);
            this.cascadeGrid.appendChild(item);
        });
        
        // Render pagination
        this.renderCascadePagination();
        this.updateCascadePaginationInfo();
    }

    createCascadeItem(image, index) {
        const item = Utils.dom.createElement('div', 'cascade-item');
        
        // Add classes for hidden/deleted states
        const isHidden = image.visible === false;
        const isDeleted = image.deleted === true;
        
        if (isHidden) item.classList.add('hidden');
        if (isDeleted) item.classList.add('deleted');
        
        if (this.cascadeEditMode) {
            item.classList.add('edit-mode');
            item.draggable = true;
        }
        
        const img = Utils.dom.createElement('img', 'cascade-image', {
            src: API.images.getThumbnailUrl(this.currentPost.post_id, image.filename, 'medium'),
            alt: image.filename,
            loading: 'lazy'
        });
        
        // Error fallback
        img.addEventListener('error', () => {
            img.src = API.images.getContentImageUrl(this.currentPost.post_id, image.filename);
        });
        
        const overlay = Utils.dom.createElement('div', 'cascade-item-overlay');
        
        if (this.cascadeEditMode) {
            const dragHandle = Utils.dom.createElement('div', 'drag-handle', {
                innerHTML: '<i class="fas fa-grip-vertical"></i>'
            });
            
            const controls = Utils.dom.createElement('div', 'cascade-controls-overlay');
            
            const visibilityBtn = Utils.dom.createElement('button', 'control-btn visibility-btn', {
                innerHTML: isHidden ? '👁️' : '🙈',
                title: isHidden ? 'Show' : 'Hide'
            });
            
            const deleteBtn = Utils.dom.createElement('button', 'control-btn delete-btn', {
                innerHTML: '🗑️',
                title: 'Delete'
            });
            
            controls.appendChild(visibilityBtn);
            controls.appendChild(deleteBtn);
            
            overlay.textContent = `#${index + 1}`;
            
            item.appendChild(dragHandle);
            item.appendChild(controls);
        } else {
            overlay.textContent = image.filename;
        }
        
        item.appendChild(img);
        item.appendChild(overlay);
        
        // Event listeners
        if (!this.cascadeEditMode) {
            item.addEventListener('click', () => {
                // Use the global index directly, not findIndex which might fail with pagination/filtering
                this.currentImageIndex = index;
                this.switchToSingle();
            });
        } else {
            this.addDragEvents(item, index);
            this.addEditModeEvents(item, image, index);
        }
        
        item.dataset.index = index;
        item.dataset.filename = image.filename;
        
        return item;
    }

    renderCascadeEmptyState() {
        const emptyState = Utils.dom.createElement('div', 'cascade-loading', {
            innerHTML: `
                <div style="grid-column: 1 / -1; text-align: center; padding: 2rem; color: var(--text-muted);">
                    <i class="fas fa-images" style="font-size: 3rem; margin-bottom: 1rem; opacity: 0.5;"></i>
                    <h3>No images found</h3>
                    <p>This post doesn't contain any images.</p>
                </div>
            `
        });
        this.cascadeGrid.appendChild(emptyState);
    }

    renderCascadePagination() {
        if (!this.cascadePagination) return;
        
        this.cascadePagination.innerHTML = '';
        
        const totalPages = Math.ceil(this.cascadeImages.length / this.cascadeItemsPerPage);
        if (totalPages <= 1) return;
        
        // Previous button
        const prevBtn = this.createCascadePaginationButton(
            '<i class="fas fa-chevron-left"></i>',
            this.cascadePage - 1,
            this.cascadePage <= 1
        );
        this.cascadePagination.appendChild(prevBtn);
        
        // Page numbers
        const { start, end } = this.getPaginationRange(this.cascadePage, totalPages);
        
        for (let i = start; i <= end; i++) {
            const btn = this.createCascadePaginationButton(
                i.toString(),
                i,
                false,
                i === this.cascadePage
            );
            this.cascadePagination.appendChild(btn);
        }
        
        // Next button
        const nextBtn = this.createCascadePaginationButton(
            '<i class="fas fa-chevron-right"></i>',
            this.cascadePage + 1,
            this.cascadePage >= totalPages
        );
        this.cascadePagination.appendChild(nextBtn);
    }

    createCascadePaginationButton(text, page, disabled = false, active = false) {
        const btn = Utils.dom.createElement('button', 'pagination-btn', {
            innerHTML: text,
            disabled: disabled
        });
        
        if (active) btn.classList.add('active');
        
        if (!disabled) {
            btn.addEventListener('click', () => this.goToCascadePage(page));
        }
        
        return btn;
    }

    updateCascadePaginationInfo() {
        if (!this.cascadePaginationInfo) return;
        
        const total = this.cascadeImages.length;
        const start = Math.min((this.cascadePage - 1) * this.cascadeItemsPerPage + 1, total);
        const end = Math.min(this.cascadePage * this.cascadeItemsPerPage, total);
        
        this.cascadePaginationInfo.textContent = 
            total > 0 ? `Showing ${start}-${end} of ${total} images` : 'No images to display';
    }

    getPaginationRange(current, total, delta = 2) {
        return {
            start: Math.max(1, current - delta),
            end: Math.min(total, current + delta)
        };
    }

    goToCascadePage(page) {
        const totalPages = Math.ceil(this.cascadeImages.length / this.cascadeItemsPerPage);
        if (page >= 1 && page <= totalPages && page !== this.cascadePage) {
            this.cascadePage = page;
            this.renderCascade();
            
            // Scroll to top of cascade container
            this.cascadeGrid.scrollTop = 0;
        }
    }

    setCascadeImageSize(size) {
        this.cascadeImageSize = size;
        this.updateCascadeImageSize();
        this.savePreferences();
    }

    updateCascadeImageSize() {
        if (this.cascadeGrid) {
            this.cascadeGrid.style.setProperty('--image-size', `${this.cascadeImageSize}px`);
        }
    }

    async toggleCascadeEditMode() {
        this.cascadeEditMode = !this.cascadeEditMode;
        this.cascadeEditModeBtn.classList.toggle('active', this.cascadeEditMode);
        
        if (this.cascadeEditMode) {
            this.cascadeEditModeBtn.innerHTML = '<i class="fas fa-check"></i> Done';
            statusManager.showInfo('Drag images to reorder, toggle visibility, or delete images');
        } else {
            this.cascadeEditModeBtn.innerHTML = '<i class="fas fa-edit"></i> Edit';
            await this.saveCascadeOrder();
        }
        
        // Reload images with new visibility settings
        await this.loadPostImages(this.currentPost);
    }

    addDragEvents(item, index) {
        item.addEventListener('dragstart', (e) => {
            item.classList.add('dragging');
            e.dataTransfer.setData('text/plain', index.toString());
        });
        
        item.addEventListener('dragend', () => {
            item.classList.remove('dragging');
        });
        
        item.addEventListener('dragover', (e) => {
            e.preventDefault();
        });
        
        item.addEventListener('drop', (e) => {
            e.preventDefault();
            const draggedIndex = parseInt(e.dataTransfer.getData('text/plain'));
            const targetIndex = parseInt(item.dataset.index);
            
            if (draggedIndex !== targetIndex) {
                this.reorderCascadeImages(draggedIndex, targetIndex);
            }
        });
    }
    
    addEditModeEvents(item, image, index) {
        const visibilityBtn = item.querySelector('.visibility-btn');
        const deleteBtn = item.querySelector('.delete-btn');
        
        if (visibilityBtn) {
            visibilityBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.toggleImageVisibility(image, index);
            });
        }
        
        if (deleteBtn) {
            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                await this.deleteImage(image, index);
            });
        }
    }

    reorderCascadeImages(fromIndex, toIndex) {
        // Calculate global indices
        const pageStartIndex = (this.cascadePage - 1) * this.cascadeItemsPerPage;
        const globalFromIndex = pageStartIndex + fromIndex;
        const globalToIndex = pageStartIndex + toIndex;
        
        // Move the image
        const movedImage = this.cascadeImages.splice(globalFromIndex, 1)[0];
        this.cascadeImages.splice(globalToIndex, 0, movedImage);
        
        // Update the current images array to match
        this.currentImages = [...this.cascadeImages];
        
        // Re-render cascade
        this.renderCascade();
    }
    
    async toggleImageVisibility(image, index) {
        const newVisibility = !image.visible;
        
        try {
            const result = await API.cascade.updateImageMetadata(
                this.currentPost.post_id, 
                image.filename, 
                { visible: newVisibility }
            );
            
            if (result.success) {
                // Update local state
                image.visible = newVisibility;
                this.cascadeImages[index].visible = newVisibility;
                
                // Re-render current view
                this.renderCascade();
                
                statusManager.showSuccess(`Image ${newVisibility ? 'shown' : 'hidden'}`);
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('Failed to toggle image visibility:', error);
            statusManager.showError('Failed to update image visibility: ' + error.message);
        }
    }
    
    async deleteImage(image, index) {
        if (!confirm(`Delete ${image.filename}? This will hide it from all views.`)) {
            return;
        }
        
        try {
            const result = await API.cascade.updateImageMetadata(
                this.currentPost.post_id, 
                image.filename, 
                { deleted: true }
            );
            
            if (result.success) {
                // Remove from current arrays if not in edit mode
                if (!this.cascadeEditMode) {
                    this.currentImages.splice(index, 1);
                    this.cascadeImages.splice(index, 1);
                    
                    // Adjust current index if needed
                    if (this.currentImageIndex >= this.currentImages.length) {
                        this.currentImageIndex = Math.max(0, this.currentImages.length - 1);
                    }
                } else {
                    // In edit mode, just mark as deleted
                    image.deleted = true;
                    this.cascadeImages[index].deleted = true;
                }
                
                // Re-render current view
                this.renderCascade();
                
                statusManager.showSuccess('Image deleted');
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('Failed to delete image:', error);
            statusManager.showError('Failed to delete image: ' + error.message);
        }
    }

    async saveCascadeOrder() {
        try {
            const imageOrder = this.cascadeImages.map((img, index) => ({
                filename: img.filename,
                order: index
            }));
            
            const result = await API.cascade.updateOrder(this.currentPost.post_id, imageOrder);
            
            if (result.success) {
                statusManager.showSuccess('Image order saved');
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('Failed to save cascade order:', error);
            statusManager.showError('Failed to save image order: ' + error.message);
        }
    }

    // Preloading functionality
    startPreloading() {
        // Clear existing queue
        this.preloadQueue = [];
        
        // Add adjacent images to preload queue
        this.preloadAdjacentImages();
        
        // Start processing queue
        this.processPreloadQueue();
    }

    preloadAdjacentImages() {
        const indicesToPreload = [];
        
        // Current image
        indicesToPreload.push(this.currentImageIndex);
        
        // Next and previous images
        for (let i = 1; i <= 3; i++) {
            const nextIndex = this.currentImageIndex + i;
            const prevIndex = this.currentImageIndex - i;
            
            if (nextIndex < this.currentImages.length) {
                indicesToPreload.push(nextIndex);
            }
            if (prevIndex >= 0) {
                indicesToPreload.push(prevIndex);
            }
        }
        
        // Add to queue if not already preloaded
        indicesToPreload.forEach(index => {
            const image = this.currentImages[index];
            if (image) {
                const imageUrl = API.images.getContentImageUrl(this.currentPost.post_id, image.filename);
                if (!this.preloadCache.has(imageUrl) && !this.preloadQueue.includes(imageUrl)) {
                    this.preloadQueue.unshift(imageUrl); // Add to front for priority
                }
            }
        });
    }

    async processPreloadQueue() {
        if (this.preloadQueue.length === 0) return;
        
        const imageUrl = this.preloadQueue.shift();
        if (!imageUrl) return;
        
        try {
            const img = await Utils.image.preload(imageUrl);
            this.preloadCache.set(imageUrl, img);
            
            // Continue processing queue with a small delay
            setTimeout(() => this.processPreloadQueue(), 100);
        } catch (error) {
            console.warn('Failed to preload image:', imageUrl, error);
            // Continue with next image
            setTimeout(() => this.processPreloadQueue(), 100);
        }
    }

    // Event handlers
    onImageLoad() {
        // Image loaded successfully
        this.singleImage.style.opacity = '1';
    }

    onImageError() {
        // Image failed to load
        console.error('Failed to load image:', this.singleImage.src);
        statusManager.showError('Failed to load image');
    }

    handleKeyboard(e) {
        // Only handle keyboard events when modals are open
        const isSingleImageOpen = this.singleImageModal && this.singleImageModal.classList.contains('active');
        const isCascadeOpen = this.cascadeModal && this.cascadeModal.classList.contains('active');
        
        if (!isSingleImageOpen && !isCascadeOpen) return;
        
        // Don't handle when input elements are focused
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        
        switch (e.key) {
            case 'Escape':
                if (isSingleImageOpen) this.closeModal('singleImageModal');
                if (isCascadeOpen) this.closeModal('cascadeModal');
                break;
                
            case 'ArrowLeft':
                if (isSingleImageOpen) {
                    e.preventDefault();
                    this.previousImage();
                }
                break;
                
            case 'ArrowRight':
                if (isSingleImageOpen) {
                    e.preventDefault();
                    this.nextImage();
                }
                break;
                
            case 'Space':
                if (isSingleImageOpen) {
                    e.preventDefault();
                    this.nextImage();
                }
                break;
                
            case 'Home':
                if (isSingleImageOpen) {
                    e.preventDefault();
                    this.currentImageIndex = 0;
                    this.displayCurrentImage();
                }
                break;
                
            case 'End':
                if (isSingleImageOpen) {
                    e.preventDefault();
                    this.currentImageIndex = this.currentImages.length - 1;
                    this.displayCurrentImage();
                }
                break;
                
            case 'c':
                if (isSingleImageOpen) {
                    e.preventDefault();
                    this.switchToCascade();
                }
                break;
                
            case 's':
                if (isCascadeOpen) {
                    e.preventDefault();
                    this.switchToSingle();
                }
                break;
                
            case 'e':
                if (isCascadeOpen && !this.cascadeEditMode) {
                    e.preventDefault();
                    this.toggleCascadeEditMode();
                }
                break;
        }
    }

    setLoading(loading) {
        this.isLoading = loading;
        
        // Show loading state in single image view
        if (this.singleImage) {
            this.singleImage.style.opacity = loading ? '0.5' : '1';
        }
        
        // Show loading state in cascade view
        if (this.cascadeGrid && loading) {
            this.cascadeGrid.innerHTML = this.createCascadeLoadingState();
        }
    }

    createCascadeLoadingState() {
        const itemCount = Math.min(this.cascadeItemsPerPage, 20);
        let loadingHtml = '';
        
        for (let i = 0; i < itemCount; i++) {
            loadingHtml += '<div class="cascade-loading-item"></div>';
        }
        
        return `<div class="cascade-loading">${loadingHtml}</div>`;
    }
}

// Export for use in main.js
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Viewer;
}