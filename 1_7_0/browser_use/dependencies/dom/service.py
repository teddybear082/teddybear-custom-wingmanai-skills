import gc
import logging
from importlib import resources
from typing import Optional

from selenium import webdriver
from selenium.webdriver.common.by import By

from .history_tree_processor.view import Coordinates
from .views import (
	CoordinateSet,
	DOMBaseNode,
	DOMElementNode,
	DOMState,
	DOMTextNode,
	SelectorMap,
	ViewportInfo,
)

logger = logging.getLogger(__name__)


class DomService:
	def __init__(self, driver: webdriver):
		self.driver = driver
		self.xpath_cache = {}

		self.js_code = DOM_SCRIPT

	# region - Clickable elements

	async def get_clickable_elements(
		self,
		highlight_elements: bool = True,
		focus_element: int = -1,
		viewport_expansion: int = 0,
	) -> DOMState:
		element_tree, selector_map = await self._build_dom_tree(highlight_elements, focus_element, viewport_expansion)

		dom_state = DOMState(element_tree=element_tree, selector_map=selector_map)

		return dom_state

	async def _build_dom_tree(
		self,
		highlight_elements: bool,
		focus_element: int,
		viewport_expansion: int,
	) -> tuple[DOMElementNode, SelectorMap]:
		if self.driver.execute_script("return 1+1") != 2:
			raise ValueError('The page cannot evaluate javascript code properly')

		# NOTE: We execute JS code in the browser to extract important DOM information.
		#       The returned hash map contains information about the DOM tree and the
		#       relationship between the DOM elements.
		#args = {
		#	'doHighlightElements': highlight_elements,
		#	'focusHighlightIndex': focus_element,
		#	'viewportExpansion': viewport_expansion,
		#}

		eval_page = self.driver.execute_script(self.js_code)

		js_node_map = eval_page['map']
		js_root_id = eval_page['rootId']

		selector_map = {}
		node_map = {}

		for id, node_data in js_node_map.items():
			node, children_ids = self._parse_node(node_data)
			if node is None:
				continue

			node_map[id] = node

			if isinstance(node, DOMElementNode) and node.highlight_index is not None:
				selector_map[node.highlight_index] = node

			# NOTE: We know that we are building the tree bottom up
			#       and all children are already processed.
			if isinstance(node, DOMElementNode):
				for child_id in children_ids:
					if child_id not in node_map:
						continue

					child_node = node_map[child_id]

					child_node.parent = node
					node.children.append(child_node)

		html_to_dict = node_map[js_root_id]

		del node_map
		del js_node_map
		del js_root_id

		gc.collect()

		if html_to_dict is None or not isinstance(html_to_dict, DOMElementNode):
			raise ValueError('Failed to parse HTML to dictionary')

		return html_to_dict, selector_map

	def _parse_node(
		self,
		node_data: dict,
	) -> tuple[Optional[DOMBaseNode], list[int]]:
		if not node_data:
			return None, []

		# Process text nodes immediately
		if node_data.get('type') == 'TEXT_NODE':
			text_node = DOMTextNode(
				text=node_data['text'],
				is_visible=node_data['isVisible'],
				parent=None,
			)
			return text_node, []

		# Process coordinates if they exist for element nodes
		viewport_coordinates = None
		page_coordinates = None
		viewport_info = None

		if 'viewportCoordinates' in node_data:
			viewport_coordinates = CoordinateSet(
				top_left=Coordinates(**node_data['viewportCoordinates']['topLeft']),
				top_right=Coordinates(**node_data['viewportCoordinates']['topRight']),
				bottom_left=Coordinates(**node_data['viewportCoordinates']['bottomLeft']),
				bottom_right=Coordinates(**node_data['viewportCoordinates']['bottomRight']),
				center=Coordinates(**node_data['viewportCoordinates']['center']),
				width=node_data['viewportCoordinates']['width'],
				height=node_data['viewportCoordinates']['height'],
			)
		if 'pageCoordinates' in node_data:
			page_coordinates = CoordinateSet(
				top_left=Coordinates(**node_data['pageCoordinates']['topLeft']),
				top_right=Coordinates(**node_data['pageCoordinates']['topRight']),
				bottom_left=Coordinates(**node_data['pageCoordinates']['bottomLeft']),
				bottom_right=Coordinates(**node_data['pageCoordinates']['bottomRight']),
				center=Coordinates(**node_data['pageCoordinates']['center']),
				width=node_data['pageCoordinates']['width'],
				height=node_data['pageCoordinates']['height'],
			)
		if 'viewport' in node_data:
			viewport_info = ViewportInfo(
				scroll_x=node_data['viewport']['scrollX'],
				scroll_y=node_data['viewport']['scrollY'],
				width=node_data['viewport']['width'],
				height=node_data['viewport']['height'],
			)

		element_node = DOMElementNode(
			tag_name=node_data['tagName'],
			xpath=node_data['xpath'],
			attributes=node_data.get('attributes', {}),
			children=[],
			is_visible=node_data.get('isVisible', False),
			is_interactive=node_data.get('isInteractive', False),
			is_top_element=node_data.get('isTopElement', False),
			highlight_index=node_data.get('highlightIndex'),
			shadow_root=node_data.get('shadowRoot', False),
			parent=None,
			viewport_coordinates=viewport_coordinates,
			page_coordinates=page_coordinates,
			viewport_info=viewport_info,
		)

		children_ids = node_data.get('children', [])

		return element_node, children_ids

# Compiled from 
# https://github.com/browser-use/browser-use/tree/main/browser_use/dom
# https://github.com/nanobrowser/nanobrowser/blob/master/chrome-extension/public/buildDomTree.js
# https://github.com/browser-use/browser-use/pull/1031
# https://github.com/browser-use/browser-use/pull/928/files
# https://github.com/browser-use/browser-use/pull/1078/files
DOM_SCRIPT = """
window.buildDomTree = (
  args = {
    doHighlightElements: true,
    focusHighlightIndex: -1,
    viewportExpansion: 0,
  }
) => {
  const { doHighlightElements, focusHighlightIndex, viewportExpansion } = args;
  let highlightIndex = 0; // Reset highlight index

  /**
   * Hash map of DOM nodes indexed by their highlight index.
   *
   * @type {Object<string, any>}
   */
  const DOM_HASH_MAP = {};

  const ID = { current: 0 };

  // Quick check to confirm the script receives focusHighlightIndex
  console.log("focusHighlightIndex:", focusHighlightIndex);

  const HIGHLIGHT_CONTAINER_ID = "selenium-highlight-container";

  /**
   * Highlights an element in the DOM and returns the index of the next element.
   */
  function highlightElement(element, index, parentIframe = null) {
    // Create or get highlight container
    let container = document.getElementById(HIGHLIGHT_CONTAINER_ID);
    if (!container) {
      container = document.createElement("div");
      container.id = HIGHLIGHT_CONTAINER_ID;
      container.style.position = "absolute";
      container.style.pointerEvents = "none";
      container.style.top = "0";
      container.style.left = "0";
      container.style.width = "100%";
      container.style.height = "100%";
      container.style.zIndex = "2147483647"; // Maximum z-index value

      document.body.appendChild(container);
    }

    // Generate a color based on the index
    const colors = [
      "#FF0000",
      "#00FF00",
      "#0000FF",
      "#FFA500",
      "#800080",
      "#008080",
      "#FF69B4",
      "#4B0082",
      "#FF4500",
      "#2E8B57",
      "#DC143C",
      "#4682B4",
    ];
    const colorIndex = index % colors.length;
    const baseColor = colors[colorIndex];
    const backgroundColor = `${baseColor}1A`; // 10% opacity version of the color

    // Create highlight overlay
    const overlay = document.createElement("div");
    overlay.style.position = "absolute";
    overlay.style.border = `2px solid ${baseColor}`;
    overlay.style.backgroundColor = backgroundColor;
    overlay.style.pointerEvents = "none";
    overlay.style.boxSizing = "border-box";

    // Position overlay based on element, including scroll position
    const rect = element.getBoundingClientRect();
    let top = rect.top + window.scrollY;
    let left = rect.left + window.scrollX;

    // Adjust position if element is inside an iframe
    if (parentIframe) {
      const iframeRect = parentIframe.getBoundingClientRect();
      top += iframeRect.top;
      left += iframeRect.left;
    }

    overlay.style.top = `${top}px`;
    overlay.style.left = `${left}px`;
    overlay.style.width = `${rect.width}px`;
    overlay.style.height = `${rect.height}px`;

    // Create label
    const label = document.createElement("div");
    label.className = "selenium-highlight-label";
    label.style.position = "absolute";
    label.style.background = baseColor;
    label.style.color = "white";
    label.style.padding = "1px 4px";
    label.style.borderRadius = "4px";
    label.style.fontSize = `${Math.min(12, Math.max(8, rect.height / 2))}px`; // Responsive font size
    label.textContent = index;

    // Calculate label position
    const labelWidth = 20; // Approximate width
    const labelHeight = 16; // Approximate height

    // Default position (top-right corner inside the box)
    let labelTop = top + 2;
    let labelLeft = left + rect.width - labelWidth - 2;

    // Adjust if box is too small
    if (rect.width < labelWidth + 4 || rect.height < labelHeight + 4) {
      // Position outside the box if it's too small
      labelTop = top - labelHeight - 2;
      labelLeft = left + rect.width - labelWidth;
    }

    label.style.top = `${labelTop}px`;
    label.style.left = `${labelLeft}px`;

    // Add to container
    container.appendChild(overlay);
    container.appendChild(label);

    // Store reference for cleanup
    element.setAttribute(
      "browser-user-highlight-id",
      `selenium-highlight-${index}`
    );

    return index + 1;
  }

  /**
   * Returns an XPath tree string for an element.
   */
  function getXPathTree(element, stopAtBoundary = true) {
    const segments = [];
    let currentElement = element;

    while (currentElement && currentElement.nodeType === Node.ELEMENT_NODE) {
      // Stop if we hit a shadow root or iframe
      if (
        stopAtBoundary &&
        (currentElement.parentNode instanceof ShadowRoot ||
          currentElement.parentNode instanceof HTMLIFrameElement)
      ) {
        break;
      }

      let index = 0;
      let sibling = currentElement.previousSibling;
      while (sibling) {
        if (
          sibling.nodeType === Node.ELEMENT_NODE &&
          sibling.nodeName === currentElement.nodeName
        ) {
          index++;
        }
        sibling = sibling.previousSibling;
      }

      const tagName = currentElement.nodeName.toLowerCase();
      const xpathIndex = index > 0 ? `[${index + 1}]` : "";
      segments.unshift(`${tagName}${xpathIndex}`);

      currentElement = currentElement.parentNode;
    }

    return segments.join("/");
  }

  // Helper function to check if element is accepted
  function isElementAccepted(element) {
    const leafElementDenyList = new Set([
      "svg",
      "script",
      "style",
      "link",
      "meta",
    ]);
    return !leafElementDenyList.has(element.tagName.toLowerCase());
  }

  /**
   * Checks if an element is interactive.
   */
  function isInteractiveElement(element) {
    // Immediately return false for body tag
    if (element.tagName.toLowerCase() === "body") {
      return false;
    }

    // Possible optimization from https://github.com/browser-use/browser-use/pull/1031/files
    function doesElementHaveInteractivePointer(element) {
      if (element.tagName.toLowerCase() === "html") return false;
      const style = window.getComputedStyle(element);

      let interactiveCursors = ["pointer", "move", "text", "grab", "cell"];

      if (interactiveCursors.includes(style.cursor)) return true;

      return false;
    }

    let isInteractiveCursor = doesElementHaveInteractivePointer(element);

    if (isInteractiveCursor) {
      return true;
    }
    // Base interactive elements and roles
    const interactiveElements = new Set([
      "a",
      "banner",
      "button",
      "canvas",
      "details",
      "dialog",
      "embed",
      "input",
      "label",
      "menu",
      "menuitem",
      "object",
      "pre",
      "select",
      "span",
      "summary",
      "textarea",
    ]);

    const interactiveRoles = new Set([
      "button",
      "menu",
      "menuitem",
      "link",
      "checkbox",
      "radio",
      "slider",
      "tab",
      "tabpanel",
      "textbox",
      "combobox",
      "grid",
      "listbox",
      "option",
      "progressbar",
      "scrollbar",
      "searchbox",
      "switch",
      "tree",
      "treeitem",
      "spinbutton",
      "tooltip",
      "a-button-inner",
      "a-dropdown-button",
      "click",
      "menuitemcheckbox",
      "menuitemradio",
      "a-button-text",
      "button-text",
      "button-icon",
      "button-icon-only",
      "button-text-icon-only",
      "dropdown",
      "combobox",
    ]);

    const tagName = element.tagName.toLowerCase();
    const role = element.getAttribute("role");
    const ariaRole = element.getAttribute("aria-role");
    const tabIndex = element.getAttribute("tabindex");

    // Add check for specific class
    const hasAddressInputClass = element.classList.contains(
      "address-input__container__input"
    );

    // Basic role/attribute checks
    const hasInteractiveRole =
      hasAddressInputClass ||
      interactiveElements.has(tagName) ||
      interactiveRoles.has(role) ||
      interactiveRoles.has(ariaRole) ||
      (tabIndex !== null &&
        tabIndex !== "-1" &&
        element.parentElement?.tagName.toLowerCase() !== "body") ||
      element.getAttribute("data-action") === "a-dropdown-select" ||
      element.getAttribute("data-action") === "a-dropdown-button";

    if (hasInteractiveRole) return true;

    // Get computed style
    const style = window.getComputedStyle(element);

    // Check if element has click-like styling
    // const hasClickStyling = style.cursor === 'pointer' ||
    //     element.style.cursor === 'pointer' ||
    //     style.pointerEvents !== 'none';

    // Check for event listeners
    const hasClickHandler =
      element.onclick !== null ||
      element.getAttribute("onclick") !== null ||
      element.hasAttribute("ng-click") ||
      element.hasAttribute("@click") ||
      element.hasAttribute("v-on:click");

    // Helper function to safely get event listeners
    function getEventListeners(el) {
      try {
        // Try to get listeners using Chrome DevTools API
        return window.getEventListeners?.(el) || {};
      } catch (e) {
        // Fallback: check for common event properties
        const listeners = {};

        // List of common event types to check
        const eventTypes = [
          "click",
          "mousedown",
          "mouseup",
          "touchstart",
          "touchend",
          "keydown",
          "keyup",
          "focus",
          "blur",
        ];

        for (const type of eventTypes) {
          const handler = el[`on${type}`];
          if (handler) {
            listeners[type] = [
              {
                listener: handler,
                useCapture: false,
              },
            ];
          }
        }

        return listeners;
      }
    }

    // Check for click-related events on the element itself
    const listeners = getEventListeners(element);
    const hasClickListeners =
      listeners &&
      (listeners.click?.length > 0 ||
        listeners.mousedown?.length > 0 ||
        listeners.mouseup?.length > 0 ||
        listeners.touchstart?.length > 0 ||
        listeners.touchend?.length > 0);

    // Check for ARIA properties that suggest interactivity
    const hasAriaProps =
      element.hasAttribute("aria-expanded") ||
      element.hasAttribute("aria-pressed") ||
      element.hasAttribute("aria-selected") ||
      element.hasAttribute("aria-checked");

    // Check for form-related functionality
    const isFormRelated =
      element.form !== undefined ||
      element.hasAttribute("contenteditable") ||
      style.userSelect !== "none";

    // Check if element is draggable
    const isDraggable =
      element.draggable || element.getAttribute("draggable") === "true";

    // Additional check to prevent body from being marked as interactive
    if (
      element.tagName.toLowerCase() === "body" ||
      element.parentElement?.tagName.toLowerCase() === "body"
    ) {
      return false;
    }

    return (
      hasAriaProps ||
      // hasClickStyling ||
      hasClickHandler ||
      hasClickListeners ||
      // isFormRelated ||
      isDraggable
    );
  }

  /**
   * Checks if an element is visible.
   */
  function isElementVisible(element) {
    const style = window.getComputedStyle(element);
    return (
      element.offsetWidth > 0 &&
      element.offsetHeight > 0 &&
      style.visibility !== "hidden" &&
      style.display !== "none"
    );
  }

  /**
   * Checks if an element is the top element at its position.
   */
  function isTopElement(element) {
    // Find the correct document context and root element
    let doc = element.ownerDocument;

    // If we're in an iframe, elements are considered top by default
    if (doc !== window.document) {
      return true;
    }

    // For shadow DOM, we need to check within its own root context
    const shadowRoot = element.getRootNode();
    if (shadowRoot instanceof ShadowRoot) {
      const rect = element.getBoundingClientRect();
      const point = {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };

      try {
        // Use shadow root's elementFromPoint to check within shadow DOM context
        const topEl = shadowRoot.elementFromPoint(point.x, point.y);
        if (!topEl) return false;

        // Check if the element or any of its parents match our target element
        let current = topEl;
        while (current && current !== shadowRoot) {
          if (current === element) return true;
          current = current.parentElement;
        }
        return false;
      } catch (e) {
        return true; // If we can't determine, consider it visible
      }
    }

    // Regular DOM elements
    const rect = element.getBoundingClientRect();

    // If viewportExpansion is -1, check if element is the top one at its position
    if (viewportExpansion === -1) {
      return true; // Consider all elements as top elements when expansion is -1
    }

    // Calculate expanded viewport boundaries including scroll position
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const viewportTop = -viewportExpansion + scrollY;
    const viewportLeft = -viewportExpansion + scrollX;
    const viewportBottom = window.innerHeight + viewportExpansion + scrollY;
    const viewportRight = window.innerWidth + viewportExpansion + scrollX;

    // Get absolute element position
    const absTop = rect.top + scrollY;
    const absLeft = rect.left + scrollX;
    const absBottom = rect.bottom + scrollY;
    const absRight = rect.right + scrollX;

    // Skip if element is completely outside expanded viewport
    if (
      absBottom < viewportTop ||
      absTop > viewportBottom ||
      absRight < viewportLeft ||
      absLeft > viewportRight
    ) {
      return false;
    }

    // For elements within expanded viewport, check if they're the top element
    try {
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;

      // Only clamp the point if it's outside the actual document
      const point = {
        x: centerX,
        y: centerY,
      };

      if (
        point.x < 0 ||
        point.x >= window.innerWidth ||
        point.y < 0 ||
        point.y >= window.innerHeight
      ) {
        return true; // Consider elements with center outside viewport as visible
      }

      const topEl = document.elementFromPoint(point.x, point.y);
      if (!topEl) return false;

      let current = topEl;
      while (current && current !== document.documentElement) {
        if (current === element) return true;
        current = current.parentElement;
      }
      return false;
    } catch (e) {
      return true;
    }
  }

  /**
   * Checks if a text node is visible.
   */
  function isTextNodeVisible(textNode) {
    const range = document.createRange();
    range.selectNodeContents(textNode);
    const rect = range.getBoundingClientRect();

    return (
      rect.width !== 0 &&
      rect.height !== 0 &&
      rect.top >= 0 &&
      rect.top <= window.innerHeight &&
      textNode.parentElement?.checkVisibility({
        checkOpacity: true,
        checkVisibilityCSS: true,
      })
    );
  }

  /**
   * Creates a node data object for a given node and its descendants and returns
   * the identifier of the node in the hash map or null if the node is not accepted.
   */
  function buildDomTree(node, parentIframe = null) {
    if (!node) {
      return null;
    }

    // NOTE: We skip highlight container nodes from the DOM tree
    //       by ignoring the container element itself and all its children.
    if (node.id === HIGHLIGHT_CONTAINER_ID) {
      return null;
    }

    // Special case for text nodes
    if (node.nodeType === Node.TEXT_NODE) {
      const textContent = node.textContent.trim();
      if (textContent && isTextNodeVisible(node)) {
        const id = `${ID.current++}`;

        DOM_HASH_MAP[id] = {
          type: "TEXT_NODE",
          text: textContent,
          isVisible: true,
        };

        return id;
      }
      return null;
    }

    // Check if element is accepted
    if (node.nodeType === Node.ELEMENT_NODE && !isElementAccepted(node)) {
      return null;
    }

    const nodeData = {
      tagName: node.tagName ? node.tagName.toLowerCase() : null,
      attributes: {},
      xpath:
        node.nodeType === Node.ELEMENT_NODE ? getXPathTree(node, true) : null,
      children: [],
    };

    // Add coordinates for element nodes
    if (node.nodeType === Node.ELEMENT_NODE) {
      const rect = node.getBoundingClientRect();
      const scrollX = window.scrollX;
      const scrollY = window.scrollY;

      // Viewport-relative coordinates (can be negative when scrolled)
      nodeData.viewportCoordinates = {
        topLeft: {
          x: Math.round(rect.left),
          y: Math.round(rect.top),
        },
        topRight: {
          x: Math.round(rect.right),
          y: Math.round(rect.top),
        },
        bottomLeft: {
          x: Math.round(rect.left),
          y: Math.round(rect.bottom),
        },
        bottomRight: {
          x: Math.round(rect.right),
          y: Math.round(rect.bottom),
        },
        center: {
          x: Math.round(rect.left + rect.width / 2),
          y: Math.round(rect.top + rect.height / 2),
        },
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };

      // Page-relative coordinates (always positive, relative to page origin)
      nodeData.pageCoordinates = {
        topLeft: {
          x: Math.round(rect.left + scrollX),
          y: Math.round(rect.top + scrollY),
        },
        topRight: {
          x: Math.round(rect.right + scrollX),
          y: Math.round(rect.top + scrollY),
        },
        bottomLeft: {
          x: Math.round(rect.left + scrollX),
          y: Math.round(rect.bottom + scrollY),
        },
        bottomRight: {
          x: Math.round(rect.right + scrollX),
          y: Math.round(rect.bottom + scrollY),
        },
        center: {
          x: Math.round(rect.left + rect.width / 2 + scrollX),
          y: Math.round(rect.top + rect.height / 2 + scrollY),
        },
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };

      // Add viewport and scroll information
      nodeData.viewport = {
        scrollX: Math.round(scrollX),
        scrollY: Math.round(scrollY),
        width: window.innerWidth,
        height: window.innerHeight,
      };
    }

    // Copy all attributes if the node is an element
    if (node.nodeType === Node.ELEMENT_NODE && node.attributes) {
      // Use getAttributeNames() instead of directly iterating attributes
      const attributeNames = node.getAttributeNames?.() || [];
      for (const name of attributeNames) {
        nodeData.attributes[name] = node.getAttribute(name);
      }
    }

    if (node.nodeType === Node.ELEMENT_NODE) {
      const isInteractive = isInteractiveElement(node);
      const isVisible = isElementVisible(node);
      const isTop = isTopElement(node);

      nodeData.isInteractive = isInteractive;
      nodeData.isVisible = isVisible;
      nodeData.isTopElement = isTop;

      // Highlight if element meets all criteria and highlighting is enabled
      if (isInteractive && isVisible && isTop) {
        nodeData.highlightIndex = highlightIndex++;
        if (doHighlightElements) {
          if (focusHighlightIndex >= 0) {
            if (focusHighlightIndex === nodeData.highlightIndex) {
              highlightElement(node, nodeData.highlightIndex, parentIframe);
            }
          } else {
            highlightElement(node, nodeData.highlightIndex, parentIframe);
          }
        }
      }
    }

    // Only add iframeContext if we're inside an iframe
    // if (parentIframe) {
    //     nodeData.iframeContext = `iframe[src="${parentIframe.src || ''}"]`;
    // }

    // Only add shadowRoot field if it exists
    if (node.shadowRoot) {
      nodeData.shadowRoot = true;
    }

    // Handle shadow DOM
    if (node.shadowRoot) {
      for (const child of node.shadowRoot.childNodes) {
        const domElement = buildDomTree(child, parentIframe);
        if (domElement) {
          nodeData.children.push(domElement);
        }
      }
    }

    // Handle iframes
    if (node.tagName === "IFRAME") {
      try {
        const iframeDoc = node.contentDocument || node.contentWindow.document;
        if (iframeDoc) {
          for (const child of iframeDoc.body.childNodes) {
            const domElement = buildDomTree(child, node);
            if (domElement) {
              nodeData.children.push(domElement);
            }
          }
        }
      } catch (e) {
        console.warn("Unable to access iframe:", node);
      }
    } else {
      for (const child of node.childNodes) {
        const domElement = buildDomTree(child, parentIframe);
        if (domElement) {
          nodeData.children.push(domElement);
        }
      }
      // If it's an <a> element and has no visible content, return null
      if (nodeData.tagName === 'a' && nodeData.children.length === 0) {
        return null;
      }
    }

    // NOTE: We register the node to the hash map.
    const id = `${ID.current++}`;
    DOM_HASH_MAP[id] = nodeData;

    return id;
  }

  const rootId = buildDomTree(document.body);

  return { rootId, map: DOM_HASH_MAP };
};
return window.buildDomTree({ doHighlightElements: true, focusHighlightIndex: -1, viewportExpansion: 0 });
"""