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

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { statusManager, confirmationModal };
}